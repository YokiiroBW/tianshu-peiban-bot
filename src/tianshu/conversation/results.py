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

    async def ensure_on(self, connection, event):
        ledger.event_identity(event)
        from .rbi_result_wake import ensure_on
        generation = await self.jobs.current_generation_on(connection)
        result = await ensure_on(connection, self.jobs, generation, event,
                                 initial_key="capability.result:" + event["event_id"], allow_successor=True)
        result["consumer_progress"] = await ledger.progress_on(connection, generation, event["event_id"])
        return result

    async def publish(self, connection, event):
        await self.ensure_on(connection, event)


class ResultConsumer:
    def __init__(self, database, identity, actions, memory, growth, conversation, life_worker, renderer):
        self.database, self.identity = database, identity
        self.actions, self.conversation, self.life_worker = actions, conversation, life_worker
        self.memory, self.growth,self.renderer = memory, growth,renderer
        self.jobs = conversation.jobs

    async def recovery_candidates(self, *, limit=32, cursor=None, repair_ref=None, repair_consumer=None):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
            life_worker=self.life_worker).candidates(limit=limit, cursor=cursor, repair_ref=repair_ref,
                                                     repair_consumer=repair_consumer)

    async def recover_result(self, job_id, **expected):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
                                    life_worker=self.life_worker).recover_result(job_id, **expected)

    async def authorize_local_repair(self, job_id, **expected):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
                                    life_worker=self.life_worker).authorize_local_repair(job_id, **expected)

    async def reconcile_due(self, *, limit=32):
        from .rbg_result_recovery import ResultRecovery
        return await ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
                                    life_worker=self.life_worker).reconcile_due(limit=limit)

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
                       or getattr(error, "code", None) in {"render_continuation_unconfirmed", "result_consumer_claim_lost",
                                                           "local_result_application_unconfirmed"})
            retry_at = (error.retry_at if isinstance(error, RecoveryWait) and recovery_wait_retryable(error)
                        else failure.retry_at if failure and failure.retryable else None)
            async with self.database.transaction() as connection:
                await ledger.release_on(connection, generation, result.receipt_id, name, token,
                    blocked=blocked, reason="database_commit_unknown" if commit_unknown and not atomic_not_committed else
                    "database_outcome_unconfirmed" if uncertain_database and not atomic_not_committed else
                    getattr(error, "code", type(error).__name__), retry_at=retry_at if not blocked else None)
            raise

    async def _head_changed(self, event, context, generation):
        for _ in range(3):
            current = await self.actions.get(event["action_id"], context)
            actual = current.get("receipt")
            if not actual:
                raise AppError("result_head_unavailable", status=409)
            if current["result_revision"] == event["result_revision"]:
                if actual["receipt_id"] != event["event_id"] or signature(actual) != signature(event["receipt"]):
                    raise AppError("result_receipt_revision_conflict", status=409)
                return None
            if current["result_revision"] < event["result_revision"]:
                raise AppError("result_head_regressed", status=409)
            try:
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
                    from .rbg_result_recovery import ResultRecovery
                    recovery = ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
                                              life_worker=self.life_worker)
                    await recovery._handoff_head_on(connection, generation, event, context, current)
                return current
            except AppError as error:
                if error.code != "result_head_advanced":
                    raise
        async with self.database.transaction(read_only=True) as connection:
            clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
        raise RecoveryWait("result_head_changing", clock["due_at"], dependency="result_head")

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

    async def consume(self, event, *, repair_ref=None, repair_consumer=None):
        from .rbj_result_budget import validate_repair
        repairing = validate_repair(repair_ref, repair_consumer)
        execution = current_job_execution()
        if event.get("source_invalidated"):
            if execution is not None:
                async with self.database.transaction() as connection:
                    native = await (await connection.execute(
                        "SELECT j.args FROM procrastinate_jobs j WHERE j.id=%s AND j.task_name='conversation.result' "
                        "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
                        "AND k.data_generation=%s AND k.cancelled_at IS NULL)",
                        (execution["job_id"], execution["data_generation"]))).fetchone()
                    payload = (native or {}).get("args") or {}
                    if (signature(payload.get("event")) == signature(event)
                            and payload.get("repair_ref") == repair_ref
                            and payload.get("repair_consumer") == repair_consumer):
                        return await self._confirmed_invalidation_on(connection, event, execution, payload,
                                                                    repair_ref, repair_consumer)
                raise AppError("result_invalidation_unconfirmed", status=409)
            return  # The Sources callback has already made this original unusable.
        if execution is None:
            if repairing:
                raise AppError("repair_native_execution_required", status=409)
            # Direct in-process callers have no native attempt to account for.
            return await self._consume_result(event)
        try:
            started = (await self._begin_repair_execution(event, execution, repair_ref, repair_consumer)
                       if repairing else await self._begin_execution(event, execution))
        except AppError as error:
            if error.code == "result_head_advanced":
                if await self._head_changed(event, context_from_record(event["context"]),
                                            execution["data_generation"]):
                    if repairing:
                        return await self._record_head_stop(event, execution, repair_ref, repair_consumer)
                    return
            if error.code in {"source_unavailable", "source_support_unavailable", "source_suppressed",
                              "source_withdrawn", "source_revoked"}:
                async with self.database.transaction() as connection:
                    await self.jobs.assert_generation_on(connection, execution["data_generation"])
                    await ledger.invalidate_on(connection, execution["data_generation"], event, error.code)
                    if repairing:
                        from .rbj_result_repair import evidence_on, stop_preflight_on
                        evidence = await evidence_on(connection, execution["data_generation"], event,
                                                     repair_consumer, repair_ref)
                        await stop_preflight_on(connection, self.jobs, execution["data_generation"], event, execution,
                            repair_consumer, repair_ref, decision="blocked", reason=error.code,
                            owner_id=evidence["owner_id"] if evidence else None)
                return
            raise
        if isinstance(started, dict) and started.get("state") == "covered":
            return started
        if isinstance(started, dict) and started.get("state") == "invalidated":
            return started
        if isinstance(started, dict) and started.get("state") == "preflight_stopped":
            if started.get("retry_at"):
                dependency = ("runtime:execution_history" if started["reason"] == "execution_history_more"
                              else "result_consumer")
                raise RecoveryWait(started["reason"], started["retry_at"], dependency=dependency)
            return
        if not started:
            return
        try:
            value = (await self._consume_repair_result(event, repair_consumer, started["owner_id"])
                     if repairing else await self._consume_result(event))
        except BaseException as error:
            failure = classify_database_failure(error)
            if isinstance(error, RecoveryWait):
                outcome = "wait" if recovery_wait_retryable(error) else "unknown"
            elif failure and failure.retryable:
                outcome = "wait"
            elif (getattr(error, "code", None) in {"local_result_application_unconfirmed",
                                                    "result_consumer_lease_unknown",
                                                    "result_consumer_outcome_unknown"}
                  or (not isinstance(error, Exception) or failure and failure.receipt_required
                  or isinstance(error, (PsycopgError, ConnectorException, TimeoutError, OSError))
                  and not getattr(error, "rbi_atomic_proof_checked", False))):
                outcome = "unknown"
            else:
                outcome = "failed"
            await self._finish_execution(event, execution, outcome, error=error)
            raise
        await self._finish_execution(event, execution, "completed")
        return value

    async def _confirmed_invalidation_on(self, connection, event, execution, payload, repair_ref, repair_consumer):
        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = execution["data_generation"]
        wake_key = f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
        if repair_ref is not None:
            wake_key += ":repair:" + signature([repair_ref, repair_consumer])
        result = await self.jobs.adopt_wake_on(connection, execution["job_id"],
            expected_task="conversation.result", expected_generation=generation,
            expected_payload=payload, wake_key=wake_key)
        if result.get("state") != "invalidated":
            raise AppError("result_invalidation_unconfirmed", status=409)
        return result

    async def _history_page_stop_on(self, connection, event, execution, repair_ref, repair_consumer, owner_id):
        """Save this ordinal's no-apply fact before retrying a bounded history scan."""
        from .rbj_result_repair import stop_preflight_on

        stopped = await stop_preflight_on(connection, self.jobs, execution["data_generation"], event, execution,
            repair_consumer, repair_ref, decision="blocked", reason="execution_history_more", owner_id=owner_id)
        clock = await (await connection.execute("SELECT clock_timestamp()+interval '1 second' AS due_at")).fetchone()
        stopped["retry_at"] = clock["due_at"]
        return stopped

    async def _normal_stop_on(self, connection, event, execution, *, reason, retry_at=None, display_reason=None):
        """Record only a registered normal native ordinal's proven no-apply decision."""
        from .rbi_result_wake import EXECUTION_OWNER, execution_identity

        generation = execution["data_generation"]
        observation = "result-preflight:" + signature([
            generation, event, execution["job_id"], execution["native_attempt"], reason,
        ])
        stopped = await self.jobs.record_execution_preflight_stop_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(generation, event), data_generation=generation,
            job_id=execution["job_id"], native_attempt=execution["native_attempt"],
            reason=reason, observation_ref=observation)
        if stopped.get("state") == "unregistered":
            return None  # An old or adopted job does not acquire a new admission marker.
        if stopped.get("state") != "preflight_stopped":
            raise AppError(stopped.get("reason") or "result_preflight_stop_unconfirmed", status=409)
        return {"state": "preflight_stopped", "reason": display_reason or reason, "retry_at": retry_at}

    async def _normal_budget_wait_on(self, connection, event, execution, budget):
        """Preserve real consumer and Runtime waits while scanning bounded history."""
        from .rbi_result_wake import lock_consumer_leases_on

        generation = execution["data_generation"]
        reason = budget.get("reason")
        if budget.get("history_more"):
            claims = await lock_consumer_leases_on(connection, generation, event)
            if claims["unknown"]:
                return None
            if claims["due_at"] is not None:
                return await self._normal_stop_on(connection, event, execution, reason="execution_wait",
                                                  retry_at=claims["due_at"], display_reason="consumer_running")
            clock = await (await connection.execute("SELECT clock_timestamp()+interval '1 second' AS due_at")).fetchone()
            return await self._normal_stop_on(connection, event, execution, reason="history_more",
                                              retry_at=clock["due_at"], display_reason="execution_history_more")
        if budget.get("consumer_lease_unknown"):
            return None
        if reason == "execution_wait" and budget.get("retry_at") is not None:
            return await self._normal_stop_on(connection, event, execution, reason=reason, retry_at=budget["retry_at"])
        current = (generation, execution["job_id"], execution["native_attempt"])
        foreign = any((item.get("data_generation"), item.get("job_id"), item.get("native_attempt")) != current
                      for item in budget.get("native_inflight", ()))
        if reason == "execution_in_progress" and budget.get("native_inflight_complete") is True and foreign:
            clock = await (await connection.execute("SELECT clock_timestamp()+interval '30 seconds' AS due_at")).fetchone()
            return await self._normal_stop_on(connection, event, execution, reason=reason, retry_at=clock["due_at"])
        return None

    async def _normal_block_on(self, connection, event, execution, reason):
        if reason == "execution_attempt_already_recorded":
            return None  # A real execution fact cannot be replaced by a preflight exit.
        decision = "unknown" if reason in {"history_unknown", "execution_outcome_unknown",
                                           "result_consumer_lease_unknown"} else "blocked"
        return await self._normal_stop_on(connection, event, execution, reason=decision, display_reason=reason)

    async def _cheap_admission(self, event, execution, *, repair_ref=None, repair_consumer=None):
        """Check the native envelope and budget before costly source proofs."""
        from .rbi_result_wake import (EXECUTION_OWNER, MAX_FAILURES, execution_budget_on,
                                      execution_identity, lock_consumer_leases_on, work_on)
        from .rbj_result_budget import validate_repair
        from .rbj_result_repair import completed_progress, evidence_on, progress_on, stop_preflight_on

        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = execution["data_generation"]
        repairing = repair_ref is not None
        expected_payload = {"event": event}
        if repairing:
            expected_payload.update(repair_ref=repair_ref, repair_consumer=repair_consumer)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            native = await (await connection.execute(
                "SELECT j.args,j.queueing_lock FROM procrastinate_jobs j WHERE j.id=%s "
                "AND j.task_name='conversation.result' AND EXISTS (SELECT 1 FROM runtime.job_keys k "
                "WHERE k.job_id=j.id AND k.data_generation=%s AND k.cancelled_at IS NULL)",
                (execution["job_id"], generation))).fetchone()
            if not native:
                raise AppError("original_result_job_unavailable", status=409)
            observed = await self.jobs.observe_on(connection, execution["job_id"])
            if signature(native["args"]) != signature(expected_payload):
                saved_payload = native["args"] or {}
                saved_event = saved_payload.get("event") or {}
                old_receipt, saved_receipt = event.get("receipt") or {}, saved_event.get("receipt") or {}
                unchanged = (saved_event.get("source_invalidated")
                    and ledger.event_identity(saved_event) == (action_id, receipt_id, revision)
                    and saved_event.get("context") == event.get("context")
                    and all(saved_receipt.get(key) == old_receipt.get(key)
                            for key in ("attempt_id", "execution", "effect", "delivery"))
                    and saved_payload.get("repair_ref") == repair_ref
                    and saved_payload.get("repair_consumer") == repair_consumer)
                if unchanged and observed and observed.get("source_invalidated"):
                    return await self._confirmed_invalidation_on(connection, saved_event, execution, saved_payload,
                                                                 repair_ref, repair_consumer)
                raise AppError("original_result_job_unavailable", status=409)
            if observed and observed.get("covered_by") is not None:
                return {"state": "covered", "covered_by": observed["covered_by"]}
            if not repairing and native["queueing_lock"] is None:
                wake_key = f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
                adopted = await self.jobs.adopt_wake_on(connection, execution["job_id"],
                    expected_task="conversation.result", expected_generation=generation,
                    expected_payload=expected_payload, wake_key=wake_key)
                if adopted.get("state") == "covered":
                    return adopted
                if adopted.get("covered_by") is not None:
                    return {"state": "covered", "covered_by": adopted["covered_by"]}
            original = await (await connection.execute(
                "SELECT job_id FROM runtime.job_keys WHERE key=%s AND task='conversation.result' "
                "AND data_generation=%s AND cancelled_at IS NULL",
                ("capability.result:" + receipt_id, generation))).fetchone()
            delivery = await ledger.register_on(connection, generation, event, original["job_id"] if original else None)
            budget = await execution_budget_on(connection, self.jobs, generation, event, delivery,
                current_job=execution, repair_ref=repair_ref, consumer=repair_consumer)
            selected = (budget.get("repair") or {}) if repairing else budget
            if selected.get("eligible"):
                return True
            if not repairing and budget.get("reason") == "execution_failure_budget_exhausted" \
                    and budget.get("failure_digest"):
                work = await work_on(connection, generation, event, jobs=self.jobs)
                for successor in work["jobs"]:
                    payload = successor["args"] or {}
                    if successor["status"] != "todo" or set(payload) != {"event", "repair_ref", "repair_consumer"}:
                        continue
                    try:
                        validate_repair(payload["repair_ref"], payload["repair_consumer"])
                    except AppError:
                        continue
                    bound = await evidence_on(connection, generation, event,
                                              payload["repair_consumer"], payload["repair_ref"])
                    if not bound or bound["failure_digest"] != budget["failure_digest"]:
                        continue
                    repair_budget = await self.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
                        identity=execution_identity(generation, event), data_generation=generation,
                        max_failures=MAX_FAILURES, repair_ref=payload["repair_ref"], consumer=payload["repair_consumer"])
                    repair = repair_budget.get("repair") or {}
                    if not repair.get("eligible") and repair.get("reason") not in {"repair_wait", "execution_wait"}:
                        continue
                    wake_key = f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
                    repair_key = wake_key + ":repair:" + signature([payload["repair_ref"], payload["repair_consumer"]])
                    coverage = "result-budget:" + signature([execution_identity(generation, event),
                        budget["failure_digest"], bound["defect_evidence_ref"],
                        payload["repair_ref"], payload["repair_consumer"]])
                    target_progress = next((part for part in work["progress"]
                                            if part["consumer"] == payload["repair_consumer"]), None)
                    lower_bound = max((value for value in (repair.get("retry_at"),
                        target_progress.get("rbi_retry_at") if target_progress else None) if value is not None), default=None)
                    covered = await self.jobs.cover_wake_on(connection, execution["job_id"],
                        expected_task="conversation.result", expected_generation=generation,
                        expected_payload=expected_payload, covered_by_job_id=successor["id"],
                        expected_covering_payload=payload, wake_key=repair_key, coverage_ref=coverage,
                        not_before=lower_bound)
                    if covered.get("state") == "covered":
                        return covered
            if repairing:
                evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
                progress = await progress_on(connection, generation, event, repair_consumer)
                if evidence and budget.get("history_more"):
                    claims = await lock_consumer_leases_on(connection, generation, event)
                    if claims["unknown"]:
                        return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                            repair_consumer, repair_ref, decision="unknown", reason="result_consumer_lease_unknown",
                            owner_id=evidence["owner_id"])
                    if claims["due_at"] is not None:
                        stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                            repair_consumer, repair_ref, decision="blocked", reason="consumer_running",
                            owner_id=evidence["owner_id"])
                        stopped["retry_at"] = claims["due_at"]
                        return stopped
                    return await self._history_page_stop_on(connection, event, execution, repair_ref,
                                                            repair_consumer, evidence["owner_id"])
                if evidence and completed_progress(generation, event, repair_consumer, progress,
                                                   owner_id=evidence["owner_id"]):
                    return "reconcile"
            else:
                stopped = await self._normal_budget_wait_on(connection, event, execution, budget)
                if stopped is not None:
                    return stopped
            reason = selected.get("reason") or ("repair_not_authorized" if repairing else "execution_ineligible")
            due = selected.get("retry_at")
            if repairing and reason == "execution_in_progress" and due is None:
                clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
                due = clock["due_at"]
            await connection.execute(
                "UPDATE conversation.result_deliveries SET rbi_reconcile_reason=%s,"
                "rbi_reconcile_due_at=CASE WHEN %s::timestamptz IS NOT NULL THEN %s "
                "WHEN %s IN ('execution_in_progress','execution_outcome_unknown','history_unknown') "
                "THEN now()+interval '30 seconds' ELSE NULL END "
                "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                (reason, due, due, reason, generation, receipt_id))
            if repairing:
                decision = "unknown" if reason in {"history_unknown", "execution_outcome_unknown",
                                                    "repair_outcome_unknown"} else "blocked"
                stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision=decision, reason=reason,
                    owner_id=evidence["owner_id"] if evidence else None)
                if reason in {"execution_wait", "repair_wait", "execution_in_progress"} and due is not None:
                    stopped["retry_at"] = due
                return stopped
            stopped = await self._normal_block_on(connection, event, execution, reason)
            if stopped is not None:
                return stopped
            return False

    async def _begin_execution(self, event, execution):
        """Admit only this native attempt against the receipt's shared budget."""
        from .rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_budget_on, execution_identity

        cheap = await self._cheap_admission(event, execution)
        if isinstance(cheap, dict):
            return cheap
        if not cheap:
            return False

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
                stopped = await self._normal_stop_on(connection, event, execution,
                    reason="closed" if done else "blocked", display_reason="result_already_terminal")
                if stopped is not None:
                    return stopped
                return False
            budget = await execution_budget_on(connection, self.jobs, generation, event, delivery,
                                               current_job=execution, reconcile_stranded=True)
            stopped = await self._normal_budget_wait_on(connection, event, execution, budget)
            if stopped is not None:
                return stopped
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
            stopped = await self._normal_block_on(connection, event, execution, reason)
            if stopped is not None:
                return stopped
            return False

    async def _record_head_stop(self, event, execution, repair_ref, repair_consumer):
        from .rbj_result_repair import evidence_on, stop_preflight_on

        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = execution["data_generation"]
        async with self.database.transaction() as connection:
            delivery = await (await connection.execute(
                "SELECT state,action_id,result_revision FROM conversation.result_deliveries "
                "WHERE data_generation=%s AND receipt_id=%s", (generation, receipt_id))).fetchone()
            if not delivery or (delivery["state"], delivery["action_id"], delivery["result_revision"]) != (
                    "superseded", action_id, revision):
                raise AppError("result_head_handoff_unconfirmed", status=409)
            evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
            return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                repair_consumer, repair_ref, decision="blocked", reason="newer_action_result",
                owner_id=evidence["owner_id"] if evidence else None)

    async def _begin_repair_execution(self, event, execution, repair_ref, repair_consumer):
        from .rbg_result_recovery import ResultRecovery
        from .rbj_result_repair import evidence_on, stop_preflight_on
        from .rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_budget_on, execution_identity, work_on

        cheap = await self._cheap_admission(event, execution, repair_ref=repair_ref,
                                            repair_consumer=repair_consumer)
        if isinstance(cheap, dict):
            return cheap
        if not cheap:
            return False
        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = execution["data_generation"]
        context = context_from_record(event["context"])
        recovery = ResultRecovery(self.database, self.jobs, self.identity, actions=self.actions,
                                  life_worker=self.life_worker)
        current = await recovery._head(event, context)
        if current["result_revision"] > revision:
            if not await self._head_changed(event, context, generation):
                raise AppError("result_head_regressed", status=409)
            return await self._record_head_stop(event, execution, repair_ref, repair_consumer)
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
            evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
            if not evidence:
                return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision="blocked", reason="repair_evidence_missing")
            target = await recovery._target_on(connection, generation, event, context, current,
                                               repair_consumer, evidence=evidence)
            if target["state"] != "not_applied":
                reconciled = await recovery._terminal_target_on(connection, generation, event, repair_consumer,
                                                                target, evidence=evidence)
                decision = ("closed" if reconciled["state"] == "verified" else
                            "unknown" if target["state"] == "unknown" or reconciled.get("outcome") == "unknown"
                            else "blocked")
                stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision=decision, reason=reconciled["reason"],
                    owner_id=evidence["owner_id"])
                if reconciled["state"] == "waiting":
                    due = reconciled.get("due_at")
                    if due is None and reconciled.get("reason") == "execution_in_progress":
                        clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
                        due = clock["due_at"]
                    if due is not None:
                        stopped["retry_at"] = due
                return stopped
            if cheap == "reconcile":
                # An unresolved prior execution cannot authorize another apply.
                return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision="unknown", reason="execution_outcome_unknown",
                    owner_id=evidence["owner_id"])
            delivery = await (await connection.execute(
                "SELECT * FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
                (generation, receipt_id))).fetchone()
            budget = await execution_budget_on(connection, self.jobs, generation, event, delivery,
                current_job=execution, reconcile_stranded=True, repair_ref=repair_ref, consumer=repair_consumer)
            if budget.get("consumer_running_due_at") is not None:
                stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision="blocked", reason="consumer_running",
                    owner_id=evidence["owner_id"])
                stopped["retry_at"] = budget["consumer_running_due_at"]
                return stopped
            if budget.get("consumer_lease_unknown"):
                return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision="unknown", reason="result_consumer_lease_unknown",
                    owner_id=evidence["owner_id"])
            if budget.get("history_more"):
                return await self._history_page_stop_on(connection, event, execution, repair_ref,
                                                        repair_consumer, evidence["owner_id"])
            if not budget.get("failure_digest") or budget["failure_digest"] != evidence["failure_digest"]:
                return await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision="blocked", reason="repair_failure_history_changed",
                    owner_id=evidence["owner_id"])
            selected = budget.get("repair") or {}
            if not selected.get("eligible"):
                reason = selected.get("reason") or "repair_not_authorized"
                decision = "unknown" if reason in {"history_unknown", "execution_outcome_unknown",
                                                    "repair_outcome_unknown"} else "blocked"
                stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision=decision, reason=reason, owner_id=evidence["owner_id"])
                due = selected.get("retry_at")
                if reason == "execution_in_progress" and due is None:
                    clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
                    due = clock["due_at"]
                if reason in {"execution_wait", "repair_wait", "execution_in_progress"} and due:
                    stopped["retry_at"] = due
                return stopped
            work = await work_on(connection, generation, event, jobs=self.jobs)
            wake_key = f"conversation.result:{generation}:{action_id}:{revision}:{receipt_id}"
            repair_key = wake_key + ":repair:" + signature([repair_ref, repair_consumer])
            payload = {"event": event, "repair_ref": repair_ref, "repair_consumer": repair_consumer}
            coverage = "result-budget:" + signature([execution_identity(generation, event),
                budget["failure_digest"], evidence["defect_evidence_ref"], repair_ref, repair_consumer])
            for old in work["jobs"]:
                if old["status"] != "todo" or set(old["args"] or {}) != {"event"}:
                    continue
                covered = await self.jobs.cover_wake_on(connection, old["id"],
                    expected_task="conversation.result", expected_generation=generation,
                    expected_payload=old["args"], covered_by_job_id=execution["job_id"],
                    expected_covering_payload=payload, wake_key=repair_key, coverage_ref=coverage)
                if covered.get("state") == "waiting":
                    stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                        repair_consumer, repair_ref, decision="blocked", reason=covered.get("reason") or "wake_cover_partial",
                        owner_id=evidence["owner_id"])
                    clock = await (await connection.execute("SELECT now()+interval '1 second' AS due_at")).fetchone()
                    stopped["retry_at"] = clock["due_at"]
                    return stopped
            started = await self.jobs.begin_execution_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(generation, event), data_generation=generation,
                job_id=execution["job_id"], native_attempt=execution["native_attempt"],
                max_failures=MAX_FAILURES, repair_ref=repair_ref, consumer=repair_consumer)
            if not started.get("eligible"):
                reason = started.get("reason") or "repair_not_authorized"
                if reason == "execution_attempt_already_recorded":
                    return False
                decision = "unknown" if reason in {"history_unknown", "execution_outcome_unknown",
                                                    "repair_outcome_unknown"} else "blocked"
                stopped = await stop_preflight_on(connection, self.jobs, generation, event, execution,
                    repair_consumer, repair_ref, decision=decision, reason=reason, owner_id=evidence["owner_id"])
                due = started.get("retry_at")
                if reason == "execution_in_progress" and due is None:
                    clock = await (await connection.execute("SELECT now()+interval '30 seconds' AS due_at")).fetchone()
                    due = clock["due_at"]
                if reason in {"execution_wait", "repair_wait", "execution_in_progress"} and due:
                    stopped["retry_at"] = due
                return stopped
            await connection.execute(
                "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,result_revision) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (generation, receipt_id, repair_consumer, action_id, revision))
            return {"owner_id": evidence["owner_id"]}

    async def _consume_repair(self, event, consumer, owner_id):
        """Only the frozen original Life callback is allowed on a repair job."""
        action_id, receipt_id, revision = ledger.event_identity(event)
        generation = current_execution_generation()
        if generation is None:
            raise AppError("repair_native_execution_required", status=409)
        context = context_from_record(event["context"])
        result = receipt(event["receipt"])
        current = await self.actions.get(action_id, context)
        if await self._head_changed(event, context, generation):
            return
        action = current["action"]
        refs = unique_refs(source_refs(event.get("source_refs", ())),
                           source_refs(action.get("source_refs", ())), result_sources(result))
        await self.identity.check(context, refs)
        observed_at = (datetime.fromisoformat(event["result_observed_at"])
                       if event.get("result_observed_at") else current["result_observed_at"])

        async def fence(connection):
            await self.actions.assert_result_head_on(connection, context, action_id=action_id,
                expected_receipt=event["receipt"], result_revision=revision,
                data_generation=generation, source_refs=refs)

        method = (self.life_worker._activity_result if consumer == "life_activity"
                  else self.life_worker._commitment_result)

        async def work(complete):
            await method(context, result, revision, observed_at, action["capability_id"],
                         on_complete=complete, expected_owner_id=owner_id)

        await self._branch(generation, result, consumer, work, atomic=True, fence=fence, source_refs=refs)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            await ledger.finish_on(connection, generation, receipt_id)

    async def _consume_repair_result(self, event, consumer, owner_id):
        try:
            return await self._consume_repair(event, consumer, owner_id)
        except AppError as error:
            generation = current_execution_generation()
            if error.code == "result_head_advanced":
                if await self._head_changed(event, context_from_record(event["context"]), generation):
                    return
                raise
            if error.code not in {"source_unavailable", "source_support_unavailable", "source_suppressed",
                                   "source_withdrawn", "source_revoked"}:
                raise
            async with self.database.transaction() as connection:
                await self.jobs.assert_generation_on(connection, generation)
                await ledger.invalidate_on(connection, generation, event, error.code)

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
