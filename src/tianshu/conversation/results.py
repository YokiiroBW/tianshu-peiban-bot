"""Action results join the existing queue and resume each durable consumer."""
from datetime import datetime
from uuid import uuid4

from procrastinate.exceptions import ConnectorException
from psycopg import Error as PsycopgError

from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation, current_job_execution
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
        from .rbi_result_wake import ensure_on
        generation = await self.jobs.current_generation_on(connection)
        await ensure_on(connection, self.jobs, generation, event,
                        initial_key="capability.result:" + event["event_id"], allow_successor=True)


class ResultConsumer:
    def __init__(self, database, identity, actions, memory, growth, conversation, life_worker, renderer):
        self.database, self.identity = database, identity
        self.actions, self.conversation, self.life_worker = actions, conversation, life_worker
        self.memory, self.growth,self.renderer = memory, growth,renderer
        self.jobs = conversation.jobs

    async def recovery_candidates(self, *, limit=32, cursor=None):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions).candidates(limit=limit, cursor=cursor)

    async def recover_result(self, job_id, **expected):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions).recover_result(job_id, **expected)

    async def reconcile_due(self, *, limit=32):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions).reconcile_due(limit=limit)

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

    async def _branch(self, generation, result, name, work, *, atomic=False, fence=None, source_refs=()):
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            token, previous = await ledger.claim_on(connection, generation, result.receipt_id, name)
        if token is None:
            return previous["proof"] or {}

        fenced = {}

        async def head_fence(connection):
            if fence is None:
                raise AppError("result_head_fence_unavailable", status=503)
            await fence(connection)
            xid = await (await connection.execute("SELECT pg_current_xact_id()::text AS xid")).fetchone()
            fenced[id(connection)] = xid["xid"]

        async def complete(connection, disposition="done", *, reason=None, proof=None):
            await self.jobs.assert_generation_on(connection, generation)
            xid = await (await connection.execute("SELECT pg_current_xact_id()::text AS xid")).fetchone()
            if fenced.get(id(connection)) != xid["xid"]:
                raise AppError("result_head_fence_missing", status=409, details={"consumer": name})
            await ledger.complete_on(connection, generation, result.receipt_id, name, token,
                                     disposition, reason=reason, proof=proof)

        async def begin(connection):
            await self.jobs.assert_generation_on(connection, generation)
        head_fence.source_refs = tuple(source_refs)
        head_fence.action_id = result.action_id
        head_fence.action_ids = frozenset({result.action_id})
        complete.begin = begin
        complete.fence = head_fence
        complete.source_refs = tuple(source_refs)

        try:
            outcome = await work(complete)
            if not atomic:
                disposition, reason, proof = outcome
                async with self.database.transaction() as connection:
                    await head_fence(connection)
                    if name == "renderer" and proof and proof.get("release"):
                        await self.jobs.enqueue(connection, JobSpec("conversation.expression_release",
                            f"result-render-release:{generation}:{result.receipt_id}:{proof['expression_id']}:{uuid4()}",
                            {"expression_id": proof["expression_id"]}, ServiceClass.INTERACTIVE,
                            subject_id=proof["actor_id"], data_generation=generation),
                            queueing_lock=f"expression-release:{proof['expression_id']}")
                    await complete(connection, disposition, reason=reason, proof=proof)
                return proof or {}
            async with self.database.transaction(read_only=True) as connection:
                recorded = await (await connection.execute(
                    "SELECT disposition,proof FROM conversation.result_consumer_progress "
                    "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s",
                    (generation, result.receipt_id, name))).fetchone()
            if not recorded or recorded["disposition"] not in ledger.TERMINAL:
                raise AppError("result_consumer_proof_missing", status=409, details={"consumer": name})
            return outcome or recorded["proof"] or {}
        except BaseException as error:
            if not isinstance(error, Exception):
                # Cancellation can interrupt a commit or an external call.
                # Keep the original token for proof-based reconciliation.
                raise
            failure = classify_database_failure(error)
            commit_unknown = bool(failure and failure.receipt_required)
            uncertain_database = commit_unknown or (isinstance(error, (PsycopgError, ConnectorException, TimeoutError, OSError))
                and not (failure and failure.retryable))
            atomic_not_committed = False
            if uncertain_database and isinstance(error, Exception):
                # The atomic Life/Memory callback records its proof in the same
                # transaction. Read it before ever considering another apply.
                async with self.database.transaction(read_only=True) as connection:
                    previous = await (await connection.execute(
                        "SELECT disposition,proof,owner_token FROM conversation.result_consumer_progress "
                        "WHERE data_generation=%s AND receipt_id=%s AND consumer=%s",
                        (generation, result.receipt_id, name))).fetchone()
                if previous and previous["disposition"] in ledger.TERMINAL:
                    return previous["proof"] or {}
                # These callbacks commit their domain effect and this proof in
                # one transaction. A still-owned nonterminal row proves that
                # transaction did not commit; it says nothing about unrelated
                # remote side effects or non-atomic renderer/dialogue work.
                atomic_not_committed = bool(atomic and previous and previous["disposition"] == "running"
                                            and previous["owner_token"] == token)
                if atomic_not_committed:
                    try:
                        error.rbi_atomic_proof_checked = True
                    except (AttributeError, TypeError):
                        pass
            blocked = ((isinstance(error, RecoveryWait) and not recovery_wait_retryable(error))
                       or uncertain_database and not atomic_not_committed
                       or getattr(error, "code", None) in {"render_continuation_unconfirmed", "result_consumer_claim_lost"})
            retry_at = (error.retry_at if isinstance(error, RecoveryWait) and recovery_wait_retryable(error)
                        else failure.retry_at if failure and failure.retryable else None)
            async with self.database.transaction() as connection:
                await ledger.release_on(connection, generation, result.receipt_id, name, token,
                    blocked=blocked, reason="database_commit_unknown" if commit_unknown and not atomic_not_committed else
                    "database_outcome_unconfirmed" if uncertain_database and not atomic_not_committed else
                    getattr(error, "code", type(error).__name__), retry_at=retry_at if not blocked else None)
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
            await self.actions.assert_result_head_on(connection, context, action_id=event["action_id"],
                expected_receipt=actual, result_revision=current["result_revision"],
                data_generation=generation, source_refs=refs)
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
        execution = current_job_execution()
        if execution is None:
            # Direct in-process callers have no native attempt to account for.
            return await self._consume_result(event)
        try:
            started = await self._begin_execution(event, execution)
        except AppError as error:
            if error.code == "result_head_advanced":
                if await self._head_changed(event, context_from_record(event["context"]),
                                            execution["data_generation"]):
                    return
            if error.code in {"source_unavailable", "source_support_unavailable", "source_suppressed",
                              "source_withdrawn", "source_revoked"}:
                async with self.database.transaction() as connection:
                    await self.jobs.assert_generation_on(connection, execution["data_generation"])
                    await ledger.invalidate_on(connection, execution["data_generation"], event, error.code)
                return
            raise
        if not started:
            return
        try:
            value = await self._consume_result(event)
        except BaseException as error:
            failure = classify_database_failure(error)
            if isinstance(error, RecoveryWait):
                outcome = "wait" if recovery_wait_retryable(error) else "unknown"
            elif failure and failure.retryable:
                outcome = "wait"
            elif (not isinstance(error, Exception) or failure and failure.receipt_required
                  or isinstance(error, (PsycopgError, ConnectorException, TimeoutError, OSError))
                  and not getattr(error, "rbi_atomic_proof_checked", False)):
                outcome = "unknown"
            else:
                outcome = "failed"
            await self._finish_execution(event, execution, outcome, error=error)
            raise
        await self._finish_execution(event, execution, "completed")
        return value

    async def _begin_execution(self, event, execution):
        """Admit only this native attempt against the receipt's shared budget."""
        from .rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_budget_on, execution_identity

        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = execution["data_generation"]
        context = context_from_record(event["context"])
        current = await self.actions.get(action_id, context)
        refs = unique_refs(source_refs(event.get("source_refs", ())),
                           source_refs(current["action"].get("source_refs", ())),
                           result_sources(receipt(event["receipt"])))
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            if not await self.conversation.sources.validate_persistence(connection, context, refs):
                raise AppError("source_unavailable", status=403)
            await self.actions.assert_result_head_on(connection, context, action_id=action_id,
                expected_receipt=event["receipt"], result_revision=revision,
                data_generation=generation, source_refs=refs)
            original = await (await connection.execute(
                "SELECT job_id FROM runtime.job_keys WHERE key=%s AND task='conversation.result' "
                "AND data_generation=%s AND cancelled_at IS NULL",
                ("capability.result:" + receipt_id, generation))).fetchone()
            delivery = await ledger.register_on(connection, generation, event,
                                                 original["job_id"] if original else None)
            done = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
            if done or delivery["state"] != "pending":
                return False
            budget = await execution_budget_on(connection, self.jobs, generation, event, delivery,
                                               current_job=execution, reconcile_stranded=True)
            if budget.get("eligible"):
                budget = await self.jobs.begin_execution_on(connection, owner=EXECUTION_OWNER,
                    identity=execution_identity(generation, event), data_generation=generation,
                    job_id=execution["job_id"], native_attempt=execution["native_attempt"],
                    max_failures=MAX_FAILURES)
            if budget.get("eligible"):
                return True
            reason = budget.get("reason") or "execution_ineligible"
            due = budget.get("retry_at") if reason == "execution_wait" else None
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_reconcile_reason=%s,"
                "rbi_reconcile_due_at=CASE WHEN %s::timestamptz IS NOT NULL THEN %s "
                "WHEN %s IN ('execution_in_progress','execution_outcome_unknown','execution_attempt_already_recorded','history_unknown') "
                "THEN now()+interval '30 seconds' ELSE NULL END "
                "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                (reason, due, due, reason, generation, receipt_id))
            return False

    async def _finish_execution(self, event, execution, outcome, *, error=None):
        from .rbi_result_wake import EXECUTION_OWNER, execution_identity

        async with self.database.transaction() as connection:
            await self.jobs.finish_execution_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(execution["data_generation"], event),
                data_generation=execution["data_generation"], job_id=execution["job_id"],
                native_attempt=execution["native_attempt"], outcome=outcome, error=error)

    async def _consume_result(self, event):
        try:
            return await self._consume(event)
        except AppError as error:
            if error.code == "result_head_advanced":
                context = context_from_record(event["context"])
                async with self.database.transaction(read_only=True) as connection:
                    generation = current_execution_generation() or await self.jobs.current_generation_on(connection)
                if await self._head_changed(event, context, generation):
                    return
                raise
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
        refs = unique_refs(source_refs(event.get("source_refs", ())), source_refs(action.get("source_refs", ())),
                           result_sources(result))
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

        async def fence(connection):
            await self.actions.assert_result_head_on(connection, context, action_id=action_id,
                expected_receipt=event["receipt"], result_revision=revision,
                data_generation=generation, source_refs=refs)

        async def render(_complete):
            rendered = await self.renderer.on_action_result(context, result, revision,
                                                            result_fence=_complete.fence)
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
                await self.renderer.channel_result(context, row["expression_id"], receipt(expression["receipt"]),
                                                   result_fence=_complete.fence)
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

        errors = []

        async def attempt(name, work, *, atomic=False):
            try:
                return await self._branch(generation, result, name, work, atomic=atomic,
                                          fence=fence, source_refs=refs)
            except Exception as error:
                failure = classify_database_failure(error)
                if (getattr(error, "code", None) in {
                        "source_unavailable", "source_support_unavailable", "source_suppressed",
                        "source_withdrawn", "source_revoked", "forbidden", "access_changed",
                        "job_generation_expired", "result_event_identity_mismatch", "result_head_advanced", "result_head_regressed",
                        "result_receipt_revision_conflict", "result_source_set_changed"}
                        or failure and failure.receipt_required and not getattr(error, "rbi_atomic_proof_checked", False)
                        or isinstance(error, (PsycopgError, ConnectorException, TimeoutError, OSError))
                        and not (failure and failure.retryable) and not getattr(error, "rbi_atomic_proof_checked", False)):
                    raise
                errors.append(error)
                return None

        render_result = await attempt("renderer", render)
        rendered = render_result.get("rendered", False) if render_result is not None else None
        if await self._head_changed(event, context, generation):
            return
        channel_result = action["capability_id"] == "channels.deliver"

        async def dialogue(_complete):
            if channel_result:
                expression_id = action.get("expression_id")
                await self.conversation.expression.channel_result(context, expression_id, result,
                                                                  result_fence=_complete.fence)
                async with self.database.transaction(read_only=True) as connection:
                    row = await (await connection.execute("SELECT receipt FROM conversation.expressions WHERE expression_id=%s AND actor_id=%s AND conversation_id=%s",
                        (expression_id, context.actor_id, context.conversation_id))).fetchone()
                if not row:
                    return "not_applicable", "expression_missing", {}
                return "done", None, {"expression_id": expression_id, "receipt_id": (row["receipt"] or {}).get("receipt_id")}
            if rendered:
                return "not_applicable", "owned_by_render_plan", {}
            await self.conversation.on_result(context, result, result_revision=revision,
                                               result_fence=_complete.fence)
            async with self.database.transaction(read_only=True) as connection:
                row = await (await connection.execute("SELECT turn_id,result_revision,last_receipt_id FROM conversation.tool_links WHERE action_id=%s", (action_id,))).fetchone()
            if not row:
                return "not_applicable", "no_tool_link", {}
            if row["result_revision"] < revision:
                raise AppError("tool_result_proof_missing", status=409)
            return "done", None, {"turn_id": row["turn_id"], "revision": row["result_revision"], "receipt_id": row["last_receipt_id"]}

        if rendered is not None:
            await attempt("dialogue", dialogue)
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
                        await complete.fence(connection)
                        await complete(connection, "not_applicable", reason="not_life_owned")
            await attempt(name, life_branch, atomic=True)
        if await self._head_changed(event, context, generation):
            return
        fallback = False
        fallback_known = rendered is not None
        if fallback_known:
            try:
                technical = await self.technical_result(action, result)
            except Exception as error:
                errors.append(error)
                fallback_known = False
            else:
                meaningful = context.actor_id is not None and action["origin_kind"] != OriginKind.OBSERVATION and not technical
                fallback = meaningful and not (delivery["owner_activity_id"] or delivery["owner_commitment_id"]) and not rendered and not channel_result

        async def detached(complete):
            if fallback:
                await self.life_worker.detached_result(context, result, action, on_complete=complete)
            else:
                async with self.database.transaction() as connection:
                    await complete.fence(connection)
                    await complete(connection, "not_applicable", reason="result_owned_elsewhere")
        if fallback_known:
            await attempt("detached", detached, atomic=True)
        if await self._head_changed(event, context, generation):
            return

        async def memory(complete):
            async with self.database.transaction() as connection:
                await complete.begin(connection)
                if fallback and result.execution in {"succeeded", "failed", "cancelled", "unknown"}:
                    if not await self.conversation.sources.validate_persistence(connection, context, refs):
                        raise AppError("source_unavailable", status=403)
                    await complete.fence(connection)
                    await self.memory.observe_episode(context, {"episode_id": "action:"+action_id,
                        "event_id": "action:"+action_id, "revision": revision, "origin_kind": "tool_result",
                        "actor_id": context.actor_id, "occurred_at": observed_at,
                        "summary": {"succeeded": "原任务已执行", "failed": "原任务未完成", "cancelled": "原任务已取消", "unknown": "原任务结果待确认"}[result.execution],
                        "source_refs": refs, "coverage": result.coverage, "action_id": action_id,
                        "receipt_id": receipt_id, "actual_result": {**result.result, "capability_id": action["capability_id"],
                            "execution": result.execution, "effect": result.effect, "delivery": result.delivery}},
                            connection=connection)
                    await complete(connection, proof={"episode_id": "action:"+action_id, "revision": revision})
                else:
                    await complete.fence(connection)
                    await complete(connection, "not_applicable", reason="result_owned_elsewhere_or_nonterminal")
        if fallback_known:
            await attempt("memory", memory, atomic=True)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            await ledger.finish_on(connection, generation, receipt_id)
        if errors:
            raise errors[0]


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
