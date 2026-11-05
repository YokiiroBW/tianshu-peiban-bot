"""Action results join the existing queue and resume each durable consumer."""
from datetime import datetime

from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
from tianshu.types import OriginKind, ServiceClass
from tianshu.capabilities.catalog import is_administrative
from tianshu.capabilities.actions import result_sources
from tianshu.capabilities.serialization import action_from
from tianshu.channels.operations import technical_api
from .codec import context_from_record, document, receipt, signature, source_refs
from . import rbg_result_ledger as ledger


class ActionResultEvents:
    def __init__(self, jobs):
        self.jobs = jobs

    async def publish(self, connection, event):
        ledger.event_identity(event)
        job_id = await self.jobs.enqueue(connection, JobSpec("conversation.result", "capability.result:"+event["event_id"],
            {"event": event}, ServiceClass.INTERACTIVE, subject_id=event["context"]["actor_id"]))
        generation = await self.jobs.current_generation_on(connection)
        await ledger.register_on(connection, generation, event, job_id)


class ResultConsumer:
    def __init__(self, database, identity, actions, memory, growth, conversation, life_worker, renderer):
        self.database, self.identity = database, identity
        self.actions, self.conversation, self.life_worker = actions, conversation, life_worker
        self.memory, self.growth,self.renderer = memory, growth,renderer
        self.jobs = conversation.jobs

    async def recovery_candidates(self, *, limit=32, cursor=None):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity).candidates(limit=limit, cursor=cursor)

    async def recover_result(self, job_id, **expected):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity).recover_result(job_id, **expected)

    async def technical_result(self, action, result):
        if action["capability_id"] == "channels.read":
            # Ingress expands the original input through this internal-only port.
            # Its audit is retained; the admitted message owns the experience.
            return True
        if action["capability_id"] == "channels.api":
            # The original calls are owned by Channels, not by model-supplied arguments.
            calls = result.result.get("calls", ())
            return bool(calls) and all(technical_api(call["api_name"]) for call in calls)
        try:
            handler = await self.actions.handler(action_from(action))
        except AppError as exc:
            if exc.code != "adapter_not_installed":
                raise
            return False
        return is_administrative(handler.definition)

    async def _branch(self, generation, result, name, work, *, atomic=False):
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            token, previous = await ledger.claim_on(connection, generation, result.receipt_id, name)
        if token is None:
            return previous["proof"] or {}

        async def complete(connection, disposition="done", *, reason=None, proof=None):
            await self.jobs.assert_generation_on(connection, generation)
            await ledger.complete_on(connection, generation, result.receipt_id, name, token,
                                     disposition, reason=reason, proof=proof)

        async def begin(connection):
            await self.jobs.assert_generation_on(connection, generation)
        complete.begin = begin

        try:
            outcome = await work(complete)
            if not atomic:
                disposition, reason, proof = outcome
                async with self.database.transaction() as connection:
                    if name == "renderer" and proof and proof.get("release"):
                        await self.jobs.enqueue(connection, JobSpec("conversation.expression_release",
                            f"result-render-release:{generation}:{result.receipt_id}:{proof['expression_id']}",
                            {"expression_id": proof["expression_id"]}, ServiceClass.INTERACTIVE,
                            subject_id=proof["actor_id"], data_generation=generation))
                    await complete(connection, disposition, reason=reason, proof=proof)
                return proof or {}
            return outcome or {}
        except BaseException as error:
            blocked = ((isinstance(error, RecoveryWait) and not recovery_wait_retryable(error))
                       or getattr(error, "code", None) in {"render_continuation_unconfirmed", "result_consumer_claim_lost"})
            async with self.database.transaction() as connection:
                await ledger.release_on(connection, generation, result.receipt_id, name, token,
                    blocked=blocked, reason=getattr(error, "code", type(error).__name__))
            raise

    async def _head_changed(self, event, context, generation):
        current = await self.actions.get(event["action_id"], context)
        actual = current.get("receipt")
        if not actual:
            raise AppError("result_head_unavailable", status=409)
        if current["result_revision"] == event["result_revision"] and actual["receipt_id"] == event["event_id"]:
            if signature(actual) != signature(event["receipt"]):
                raise AppError("result_event_identity_mismatch", status=409)
            return None
        if current["result_revision"] < event["result_revision"]:
            raise AppError("result_head_regressed", status=409)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            actual_result = receipt(actual)
            refs = unique_refs(source_refs(event.get("source_refs", ())), result_sources(receipt(event["receipt"])),
                               source_refs(current["action"].get("source_refs", ())), result_sources(actual_result))
            if not await self.conversation.sources.validate_persistence(connection, context, refs):
                raise AppError("source_unavailable", status=403)
            await ledger.queue_head_on(connection, self.jobs, generation, current)
            await ledger.supersede_on(connection, generation, event["event_id"])
        return current

    async def _verify_completed_identity(self, event, context, generation):
        """An old aggregate completion never authorizes replay or masks a known collision."""
        async with self.database.transaction(read_only=True) as connection:
            delivery = await (await connection.execute(
                "SELECT action_id,result_revision FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
                (generation, event["event_id"]))).fetchone()
            original = await (await connection.execute(
                "SELECT j.args->'event' AS event FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
                "WHERE k.key=%s AND k.data_generation=%s", ("capability.result:" + event["event_id"], generation))).fetchone()
        if delivery and (delivery["action_id"] != event["action_id"] or delivery["result_revision"] != event["result_revision"]):
            raise AppError("result_receipt_revision_conflict", status=409)
        if original and original["event"]:
            saved = original["event"]
            if saved.get("source_invalidated"):
                return
            if (saved.get("result_revision") != event["result_revision"]
                    or signature(saved.get("receipt")) != signature(event["receipt"])):
                raise AppError("result_receipt_revision_conflict", status=409)
        try:
            head = await self.actions.get(event["action_id"], context)
        except AppError:
            return  # The legacy completed record still prevents any replay.
        current = head.get("receipt") or {}
        if current.get("receipt_id") == event["event_id"] and (
                head["result_revision"] != event["result_revision"] or signature(current) != signature(event["receipt"])):
            raise AppError("result_receipt_revision_conflict", status=409)

    async def consume(self, event):
        if event.get("source_invalidated"):
            return  # The Sources callback has already made this original unusable.
        try:
            return await self._consume(event)
        except AppError as error:
            if error.code not in {"source_unavailable", "source_support_unavailable", "source_suppressed", "source_withdrawn", "source_revoked"}:
                raise
            ledger.event_identity(event)
            async with self.database.transaction() as connection:
                generation = current_execution_generation() or await self.jobs.current_generation_on(connection)
                await self.jobs.assert_generation_on(connection, generation)
                await ledger.invalidate_on(connection, generation, event, error.code)

    async def _consume(self, event):
        action_id, receipt_id, revision = ledger.event_identity(event)
        context = context_from_record(event["context"])
        result = receipt(event["receipt"])
        async with self.database.transaction(read_only=True) as connection:
            generation = current_execution_generation() or await self.jobs.current_generation_on(connection)
            previous = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
        if previous:
            await self._verify_completed_identity(event, context, generation)
            return
        await self.identity.check(context, source_refs(event.get("source_refs", ())))
        current = await self.actions.get(action_id, context)
        action = current["action"]
        refs = unique_refs(source_refs(action.get("source_refs", ())), result_sources(result))
        await self.identity.check(context, refs)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            previous = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
            delivery = None if previous else await ledger.initialize_on(connection, generation, event, action, context.actor_id)
        if previous:
            await self._verify_completed_identity(event, context, generation)
            return
        if delivery["state"] != "pending" or await self._head_changed(event, context, generation):
            return

        async def render(_complete):
            rendered = await self.renderer.on_action_result(context, result, revision)
            if not rendered:
                return "not_applicable", "no_render_plan", {"rendered": False}
            async with self.database.transaction(read_only=True) as connection:
                row = await (await connection.execute(
                    "SELECT expression_id,state,reason,steps,continuation_unconfirmed FROM conversation.expression_plans "
                    "WHERE actor_id=%s AND conversation_id=%s AND steps@>%s",
                    (context.actor_id, context.conversation_id, document([{"action_id": action_id}])))).fetchone()
                expression = await (await connection.execute(
                    "SELECT receipt FROM conversation.expressions WHERE expression_id=%s",
                    (row["expression_id"],))).fetchone() if row else None
            if not row:
                raise AppError("render_result_proof_missing", status=409)
            if row["state"] == "delivering" and row["continuation_unconfirmed"]:
                raise AppError("render_continuation_unconfirmed", status=409)
            if expression and expression["receipt"] and row["state"] in {"delivering", "waiting_delivery", "delivery_unknown"}:
                # Repair the existing channel receipt's projections; never send again.
                await self.renderer.channel_result(context, row["expression_id"], receipt(expression["receipt"]))
                async with self.database.transaction(read_only=True) as connection:
                    row = await (await connection.execute(
                        "SELECT expression_id,state,reason,steps,continuation_unconfirmed FROM conversation.expression_plans WHERE expression_id=%s",
                        (row["expression_id"],))).fetchone()
            if row["reason"] == "source_unavailable":
                return "invalidated", "source_unavailable", {"rendered": True, "expression_id": row["expression_id"]}
            step = next((item for item in row["steps"] if item.get("action_id") == action_id), None)
            if not step or step.get("result_revision", -1) < revision:
                raise AppError("render_result_proof_missing", status=409)
            return "done", None, {"rendered": True, "expression_id": row["expression_id"], "revision": step["result_revision"],
                                  "release": row["state"] in {"ready", "waiting_order"}, "actor_id": context.actor_id}

        rendered = (await self._branch(generation, result, "renderer", render)).get("rendered", False)
        if await self._head_changed(event, context, generation):
            return
        channel_result = action["capability_id"] == "channels.deliver"

        async def dialogue(_complete):
            if channel_result:
                expression_id = action.get("expression_id")
                await self.conversation.expression.channel_result(context, expression_id, result)
                async with self.database.transaction(read_only=True) as connection:
                    row = await (await connection.execute("SELECT receipt FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",
                        (expression_id, context.actor_id, context.conversation_id))).fetchone()
                if not row:
                    return "not_applicable", "expression_missing", {}
                return "done", None, {"expression_id": expression_id, "receipt_id": (row["receipt"] or {}).get("receipt_id")}
            if rendered:
                return "not_applicable", "owned_by_render_plan", {}
            await self.conversation.on_result(context, result, result_revision=revision)
            async with self.database.transaction(read_only=True) as connection:
                row = await (await connection.execute("SELECT turn_id,result_revision,last_receipt_id FROM conversation.tool_links WHERE action_id=%s", (action_id,))).fetchone()
            if not row:
                return "not_applicable", "no_tool_link", {}
            if row["result_revision"] < revision:
                raise AppError("tool_result_proof_missing", status=409)
            return "done", None, {"turn_id": row["turn_id"], "revision": row["result_revision"], "receipt_id": row["last_receipt_id"]}

        await self._branch(generation, result, "dialogue", dialogue)
        observed_at = current["result_observed_at"]
        if event.get("result_observed_at"):
            observed_at = datetime.fromisoformat(event["result_observed_at"])
        for name, owner, method in (("life_activity", delivery["owner_activity_id"], self.life_worker._activity_result),
                                    ("life_commitment", delivery["owner_commitment_id"], self.life_worker._commitment_result)):
            if await self._head_changed(event, context, generation):
                return
            async def life_branch(complete, owner=owner, method=method):
                if owner:
                    await method(context, result, revision, observed_at, action["capability_id"], on_complete=complete)
                else:
                    async with self.database.transaction() as connection:
                        await complete(connection, "not_applicable", reason="not_life_owned")
            await self._branch(generation, result, name, life_branch, atomic=True)
        if await self._head_changed(event, context, generation):
            return
        technical = await self.technical_result(action, result)
        meaningful = context.actor_id is not None and action["origin_kind"] != OriginKind.OBSERVATION and not technical
        fallback = meaningful and not (delivery["owner_activity_id"] or delivery["owner_commitment_id"]) and not rendered and not channel_result

        async def detached(complete):
            if fallback:
                await self.life_worker.detached_result(context, result, action, on_complete=complete)
            else:
                async with self.database.transaction() as connection:
                    await complete(connection, "not_applicable", reason="result_owned_elsewhere")
        await self._branch(generation, result, "detached", detached, atomic=True)
        if await self._head_changed(event, context, generation):
            return

        async def memory(complete):
            async with self.database.transaction() as connection:
                await complete.begin(connection)
                if fallback and result.execution in {"succeeded", "failed", "cancelled", "unknown"}:
                    await self.memory.observe_episode(context, {"episode_id": "action:"+action_id,
                        "event_id": "action:"+action_id, "revision": revision, "origin_kind": "tool_result",
                        "actor_id": context.actor_id, "occurred_at": observed_at,
                        "summary": {"succeeded": "原任务已执行", "failed": "原任务未完成", "cancelled": "原任务已取消", "unknown": "原任务结果待确认"}[result.execution],
                        "source_refs": refs, "coverage": result.coverage, "action_id": action_id,
                        "receipt_id": receipt_id, "actual_result": {**result.result, "capability_id": action["capability_id"],
                            "execution": result.execution, "effect": result.effect, "delivery": result.delivery}}, connection=connection)
                    await complete(connection, proof={"episode_id": "action:"+action_id, "revision": revision})
                else:
                    await complete(connection, "not_applicable", reason="result_owned_elsewhere_or_nonterminal")
        await self._branch(generation, result, "memory", memory, atomic=True)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            await ledger.finish_on(connection, generation, receipt_id)


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
