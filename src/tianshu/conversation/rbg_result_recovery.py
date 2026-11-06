"""Observe and wake original result consumers without executing an Action."""
import asyncio

from tianshu.capabilities.actions import result_sources
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from .codec import context_from_record, receipt, signature, source_refs
from . import rbg_result_ledger as ledger
from .rbg_result_ledger import event_identity
from .rbg_result_withdrawal import event_refs
from .rbi_result_wake import ensure_on, execution_budget_on, runnable_consumers, waiting_consumers, work_on
from .rbj_result_budget import eligibility, matching_life_proof, validate_repair
from .rbj_result_repair import (close_from_proof_on, evidence_on, inspect_target_on,
                                preserve_target_unknown_on, record_target_on)


class ResultRecovery:
    def __init__(self, database, jobs, identity, guard_on=None, *, actions=None, life_worker=None):
        self.database, self.jobs, self.identity, self.guard_on = database, jobs, identity, guard_on
        self.actions, self.life_worker = actions, life_worker

    async def _prepare_original(self, job_id, *, data_generation, action_id, receipt_id,
                                result_revision, material_digest=None):
        target = {"job_id": job_id}
        async with self.database.transaction(read_only=True) as connection:
            row = await self._original_on(connection, target)
            generation = await self.jobs.current_generation_on(connection)
        if not row or row["cancelled_at"] or row["data_generation"] != data_generation:
            raise AppError("original_result_job_unavailable", status=409)
        if generation != data_generation:
            raise AppError("job_generation_expired", status=409)
        event = (row["args"] or {}).get("event") or {}
        if event.get("source_invalidated"):
            raise AppError("source_withdrawn", status=409)
        if event_identity(event) != (action_id, receipt_id, result_revision):
            raise AppError("original_result_changed", status=409)
        digest = signature(event)
        if material_digest is not None and material_digest != digest:
            raise AppError("original_result_changed", status=409)
        context = context_from_record(event["context"])
        await self.identity.check(context, event_refs(event))
        current = await self._head(event, context)
        refs = unique_refs(event_refs(event), source_refs(current["action"].get("source_refs", ())),
                           result_sources(receipt(current["receipt"])))
        return event, digest, context, current, refs

    async def _fence_original_on(self, connection, job_id, generation, event, digest, context, current, refs):
        await self.jobs.assert_generation_on(connection, generation)
        row = await self._original_on(connection, {"job_id": job_id})
        if not row or row["cancelled_at"] or row["data_generation"] != generation:
            raise AppError("original_result_job_unavailable", status=409)
        saved = (row["args"] or {}).get("event") or {}
        if saved.get("source_invalidated"):
            raise AppError("source_withdrawn", status=409)
        if signature(saved) != digest or event_identity(saved) != event_identity(event):
            raise AppError("original_result_changed", status=409)
        if not await self.identity.sources.validate_persistence(connection, context, refs):
            raise AppError("source_unavailable", status=403)
        await self.actions.assert_result_head_on(connection, context, action_id=event["action_id"],
            expected_receipt=current["receipt"], result_revision=current["result_revision"],
            data_generation=generation, source_refs=refs)

    async def _target_on(self, connection, generation, event, context, current, consumer, *, evidence=None,
                         allow_blocked_recheck=False):
        delivery = await (await connection.execute(
            "SELECT action_id,result_revision,owner_resolved,owner_activity_id,owner_commitment_id "
            "FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
            (generation, event["event_id"]))).fetchone()
        if not delivery or (delivery["action_id"], delivery["result_revision"]) != (
                event["action_id"], event["result_revision"]):
            return {"state": "unknown", "reason": "result_delivery_unavailable"}
        field = "owner_activity_id" if consumer == "life_activity" else "owner_commitment_id"
        owner_id = evidence["owner_id"] if evidence else delivery[field] if delivery["owner_resolved"] else None
        return await inspect_target_on(connection, self.life_worker, context, receipt(event["receipt"]),
            event["result_revision"], generation=generation, event=event, consumer=consumer, owner_id=owner_id,
            allow_blocked_recheck=allow_blocked_recheck)

    async def _terminal_target_on(self, connection, generation, event, consumer, target, *, evidence=None):
        if target["state"] == "unknown":
            return {**target, "state": "blocked", "outcome": "unknown"}
        if target["state"] in {"blocked", "waiting"}:
            return target
        if target["state"] == "superseded":
            owner_field = "activity_id" if consumer == "life_activity" else "commitment_id"
            owner_id = evidence["owner_id"] if evidence else target.get("owner_id")
            proof = {**(target.get("proof") or {}), **({owner_field: owner_id} if owner_id else {})}
            target = await record_target_on(connection, generation, event, consumer, "superseded",
                                            reason=target["reason"], proof=proof, owner_id=owner_id)
        elif target["state"] == "verified" and not target.get("progress_recorded"):
            target = await record_target_on(connection, generation, event, consumer, "done",
                reason=target["reason"], proof=target.get("proof"),
                owner_id=evidence["owner_id"] if evidence else target.get("owner_id"))
        if target["state"] == "unknown":
            return {**target, "state": "blocked", "outcome": "unknown"}
        if target["state"] != "verified" or not evidence:
            return target
        return await close_from_proof_on(connection, self.jobs, generation, event, consumer,
            evidence["repair_ref"], target.get("proof") or {}, disposition=target.get("disposition", "done"),
            reason=target.get("terminal_reason") or target["reason"], owner_id=evidence["owner_id"])

    async def _handoff_head_on(self, connection, generation, event, context, current, *,
                               repair_ref=None, repair_consumer=None):
        evidence = target = None
        proven_unapplied = {}
        if repair_ref is not None:
            evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
            if evidence:
                target = await self._target_on(connection, generation, event, context, current,
                                               repair_consumer, evidence=evidence, allow_blocked_recheck=True)
        elif self.life_worker is not None:
            # Life owns these facts. A head advance alone is not evidence that
            # a prior direct application did not commit.
            for consumer in ("life_activity", "life_commitment"):
                part = await self._target_on(connection, generation, event, context, current, consumer,
                                             allow_blocked_recheck=True)
                if part["state"] in {"not_applied", "superseded"} and part.get("owner_id"):
                    proven_unapplied[consumer] = part
        needs_budget = bool(proven_unapplied) or bool(target and target["state"] in {"not_applied", "superseded"})
        if needs_budget:
            delivery = await (await connection.execute(
                "SELECT * FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
                (generation, event["event_id"]))).fetchone()
            budget = await execution_budget_on(connection, self.jobs, generation, event, delivery,
                                               repair_ref=repair_ref, consumer=repair_consumer)
            if not budget.get("history_complete") or budget.get("unknown_held") or budget.get("active_executions"):
                proven_unapplied.clear()
                if target and target["state"] in {"not_applied", "superseded"}:
                    target = {"state": "unknown", "reason": budget.get("reason") or "history_unknown"}
        wake = await ledger.queue_head_on(connection, self.jobs, generation, current)
        await ledger.supersede_on(connection, generation, event["event_id"])
        result = {**wake, "superseded_receipt_id": event["event_id"]}
        for consumer, part in proven_unapplied.items():
            owner_field = "activity_id" if consumer == "life_activity" else "commitment_id"
            await record_target_on(connection, generation, event, consumer, "superseded",
                reason="newer_action_result", proof={owner_field: part["owner_id"]}, owner_id=part["owner_id"])
        if proven_unapplied:
            await ledger.finish_on(connection, generation, event["event_id"])
        if target is None:
            return result
        if target["state"] == "not_applied":
            target = {"state": "superseded", "reason": "newer_action_result", "owner_id": evidence["owner_id"]}
        if target["state"] == "unknown":
            await preserve_target_unknown_on(connection, generation, event, repair_consumer, target["reason"])
            result["old_repair"] = {"state": "blocked", "outcome": "unknown", "reason": target["reason"]}
        else:
            result["old_repair"] = await self._terminal_target_on(connection, generation, event,
                                                                   repair_consumer, target, evidence=evidence)
            if result["old_repair"].get("state") == "verified":
                await ledger.finish_on(connection, generation, event["event_id"])
        return result

    async def _budget_for(self, generation, event, *, repair_ref=None, repair_consumer=None):
        """Import a bounded page of native facts before reporting eligibility."""
        action_id, receipt_id, revision = event_identity(event)
        async with self.database.transaction() as connection:
            await self.jobs.assert_generation_on(connection, generation)
            delivery = await (await connection.execute(
                "SELECT * FROM conversation.result_deliveries "
                "WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
                (generation, receipt_id))).fetchone()
            if not delivery or (delivery["action_id"], delivery["result_revision"]) != (action_id, revision):
                raise AppError("original_result_changed", status=409)
            return await execution_budget_on(connection, self.jobs, generation, event, delivery,
                                             repair_ref=repair_ref, consumer=repair_consumer)

    async def _head(self, event, context):
        if self.actions is None:
            raise AppError("action_head_unavailable", status=503)
        current = await self.actions.get(event["action_id"], context)
        actual = current.get("receipt")
        if not actual:
            raise AppError("result_head_unavailable", status=409)
        revision = current["result_revision"]
        if revision < event["result_revision"]:
            raise AppError("result_head_regressed", status=409)
        if revision == event["result_revision"] and (
                actual.get("receipt_id") != event["event_id"] or
                signature(actual) != signature(event["receipt"])):
            raise AppError("result_receipt_revision_conflict", status=409)
        return current

    async def candidates(self, *, limit=32, cursor=None, repair_ref=None, repair_consumer=None):
        validate_repair(repair_ref, repair_consumer)
        limit = max(1, min(int(limit), 250))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "WITH bounded AS (SELECT d.original_job_id AS id,j.status,j.scheduled_at,"
                "d.action_id,d.receipt_id,d.result_revision::text AS result_revision,d.data_generation,d.state AS delivery_state,"
                "(c.receipt_id IS NOT NULL) AS has_aggregate,"
                "COALESCE(k.created_at,d.created_at) AS created_at,d.original_job_id,j.args,k.cancelled_at,"
                "k.created_at AS key_created_at "
                "FROM conversation.result_deliveries d "
                "JOIN runtime.installation_generation g ON g.singleton=true AND g.id=d.data_generation "
                "LEFT JOIN procrastinate_jobs j ON j.id=d.original_job_id AND j.task_name='conversation.result' "
                "LEFT JOIN LATERAL (SELECT created_at,cancelled_at FROM runtime.job_keys k "
                "WHERE k.job_id=d.original_job_id AND k.data_generation=d.data_generation "
                "AND k.task='conversation.result' ORDER BY k.cancelled_at NULLS FIRST,k.created_at LIMIT 1) k ON true "
                "LEFT JOIN conversation.result_consumption c ON c.receipt_id=d.receipt_id "
                "WHERE d.state IN ('pending','completed','superseded','invalidated') AND d.original_job_id IS NOT NULL "
                "AND (c.receipt_id IS NULL OR (SELECT count(*) FROM conversation.result_consumer_progress p "
                "WHERE p.data_generation=d.data_generation AND p.receipt_id=d.receipt_id "
                "AND p.disposition IN ('done','not_applicable','superseded','invalidated'))<6) "
                "AND (%s::bigint IS NULL OR d.original_job_id>%s) "
                "ORDER BY CASE WHEN %s::bigint IS NULL THEN d.rbi_reconcile_checked_at END NULLS FIRST,"
                "d.original_job_id LIMIT %s) "
                "SELECT b.*,progress.consumers,progress.live_lease_until,progress.retry_due_at,progress.progress_at,"
                "wake.queued,wake.queued_due_at,wake.live_doing,observed.last_event_at,now() AS observed_at FROM bounded b "
                "LEFT JOIN LATERAL (SELECT jsonb_agg(jsonb_build_object('consumer',p.consumer,"
                "'disposition',p.disposition)) AS consumers,"
                "max(p.lease_until) FILTER (WHERE p.disposition='running' AND p.lease_until>now()) AS live_lease_until,"
                "min(p.rbi_retry_at) FILTER (WHERE p.disposition='pending' AND p.rbi_retry_at>now()) AS retry_due_at,"
                "max(p.updated_at) AS progress_at FROM conversation.result_consumer_progress p "
                "WHERE p.data_generation=b.data_generation AND p.receipt_id=b.receipt_id) progress ON true "
                "LEFT JOIN LATERAL (SELECT bool_or(j.status='todo') AS queued,"
                "min(COALESCE(j.scheduled_at,k.created_at)) FILTER (WHERE j.status='todo') AS queued_due_at,"
                "bool_or(j.status='doing' AND w.last_heartbeat>now()-interval '30 seconds') AS live_doing "
                "FROM procrastinate_jobs j JOIN runtime.job_keys k ON k.job_id=j.id "
                "LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
                "WHERE k.data_generation=b.data_generation AND k.cancelled_at IS NULL "
                "AND j.task_name='conversation.result' AND j.status IN ('todo','doing') "
                "AND j.args->'event'->>'event_id'=b.receipt_id AND j.args->'event'->>'action_id'=b.action_id "
                "AND j.args->'event'->>'result_revision'=b.result_revision "
                "AND COALESCE(j.args->'event'->>'source_invalidated','false')<>'true') wake ON true "
                "LEFT JOIN LATERAL (SELECT max(e.at) AS last_event_at FROM procrastinate_jobs j "
                "JOIN LATERAL (SELECT at FROM procrastinate_events WHERE job_id=j.id ORDER BY id DESC LIMIT 1) e ON true "
                "WHERE EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
                "AND k.data_generation=b.data_generation) AND j.task_name='conversation.result' "
                "AND j.args->'event'->>'event_id'=b.receipt_id AND j.args->'event'->>'action_id'=b.action_id "
                "AND j.args->'event'->>'result_revision'=b.result_revision) observed ON true "
                "ORDER BY b.id", (cursor, cursor, cursor, limit))).fetchall()
        result = []
        for row in rows:
            if not row["action_id"] or not row["receipt_id"] or not (row["result_revision"] or "").isdigit():
                continue
            target = {"kind": "conversation.result", "id": str(row["id"]),
                      "material_id": row["receipt_id"], "version": str(row["id"])}
            last_progress = max(value for value in (row["created_at"], row["progress_at"], row["last_event_at"]) if value)
            due_at = row["queued_due_at"] or row["live_lease_until"] or row["retry_due_at"] or last_progress
            runnable, blocked = runnable_consumers(row["consumers"] or ())
            missing = row["args"] is None or row["key_created_at"] is None or row["cancelled_at"] is not None
            withdrawn = bool(row["args"] and (row["args"].get("event") or {}).get("source_invalidated"))
            stopped = bool(blocked and not runnable)
            aggregate_unknown = row["has_aggregate"] and (len(row["consumers"] or ()) != len(ledger.CONSUMERS)
                or any(part["disposition"] not in ledger.TERMINAL for part in row["consumers"] or ()))
            event = (row["args"] or {}).get("event") or {}
            valid_identity = False
            if event and not withdrawn:
                try:
                    valid_identity = event_identity(event) == (row["action_id"], row["receipt_id"],
                                                               int(row["result_revision"]))
                except (AppError, ValueError):
                    pass
            budget, problem = None, None
            if valid_identity and not missing:
                try:
                    budget = await self._budget_for(row["data_generation"], event, repair_ref=repair_ref,
                                                    repair_consumer=repair_consumer)
                except AppError as error:
                    problem = error.code
            work = None
            if valid_identity and not missing and not problem:
                try:
                    async with self.database.transaction(read_only=True) as connection:
                        work = await work_on(connection, row["data_generation"], event, jobs=self.jobs)
                except AppError as error:
                    problem = error.code
            active_jobs = work["jobs"] if work else []
            queued = [job for job in active_jobs if job["status"] == "todo"]
            executing = bool(work and (work["running"] or any(job["status"] == "doing" for job in active_jobs)))
            queued_future = bool(queued and not executing and all(job["scheduled_at"]
                and job["scheduled_at"] > row["observed_at"] for job in queued))
            if queued:
                due_at = min((job["scheduled_at"] or row["observed_at"] for job in queued))
            elif work and work["waiting"]:
                due_at = min(item["rbi_retry_at"] for item in work["waiting"])
            diagnostics = eligibility(budget or {}, repair_ref=repair_ref, consumer=repair_consumer)
            if repair_ref and diagnostics["eligible"] and not problem:
                try:
                    checked = await self.inspect(None, {**target, "repair_ref": repair_ref,
                                                        "repair_consumer": repair_consumer})
                except AppError as error:
                    checked = {"eligible": False, "reason": error.code}
                if not checked.get("eligible"):
                    diagnostics.update(eligible=False, reason=checked.get("reason") or "result_repair_unconfirmed")
                    diagnostics["repair_eligibility"] = {**diagnostics["repair_eligibility"],
                        "eligible": False, "reason": diagnostics["reason"]}
            reason = ("original_result_job_unavailable" if missing else
                      "source_withdrawn_consumer_outcome_unknown" if withdrawn else
                      "result_consumer_outcome_unknown" if row["delivery_state"] != "pending" or aggregate_unknown else
                      "original_result_changed" if not valid_identity else problem if problem else
                      diagnostics["reason"] if not diagnostics["eligible"] else
                      "result_dependency_wait" if queued_future else
                      "result_consumer_blocked" if stopped and not repair_ref else None)
            selected = diagnostics["repair_eligibility"] if repair_ref else diagnostics["normal_eligibility"]
            if selected.get("retry_at") and (not due_at or selected["retry_at"] > due_at):
                due_at = selected["retry_at"]
            if reason == "result_dependency_wait":
                selected.update(eligible=False, reason=reason, retry_at=due_at)
            result.append({"eligible": reason is None, "reason": reason,
                "due_at": due_at, "dependency": "conversation.result",
                "outcome": "unknown" if reason in {"result_consumer_outcome_unknown",
                    "source_withdrawn_consumer_outcome_unknown", "history_unknown", "execution_outcome_unknown",
                    "repair_outcome_unknown", "local_result_application_unconfirmed"} else None,
                "error_family": "result_consumption_incomplete", "version": str(row["id"]),
                "material_id": row["receipt_id"], "role": "bot-foreground",
                "executing": executing,
                "disposition": row["delivery_state"],
                "waiting_dependency": reason in {"execution_wait", "execution_in_progress", "repair_wait", "result_dependency_wait"},
                "target": {**target, **({"repair_ref": repair_ref, "repair_consumer": repair_consumer}
                                         if repair_ref else {})},
                "normal_eligibility": diagnostics["normal_eligibility"],
                "repair_eligibility": diagnostics["repair_eligibility"]})
        return result

    async def _original_on(self, connection, target):
        job_id = target.get("job_id") or target.get("id")
        try:
            job_id = int(job_id)
        except (TypeError, ValueError):
            return None
        return await (await connection.execute(
            "SELECT j.id,j.status,j.args,k.data_generation,k.cancelled_at FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.id=%s AND j.task_name='conversation.result' "
            "ORDER BY k.cancelled_at NULLS FIRST,k.created_at,k.key LIMIT 1",
            (job_id,))).fetchone()

    async def inspect(self, _incident_or_recovery, target):
        repair_ref, repair_consumer = target.get("repair_ref"), target.get("repair_consumer")
        repairing = validate_repair(repair_ref, repair_consumer)
        async with self.database.transaction(read_only=True) as connection:
            row = await self._original_on(connection, target)
            if not row or row["cancelled_at"]:
                return {"eligible": False, "reason": "original_result_job_unavailable"}
            generation = await self.jobs.current_generation_on(connection)
            if generation != row["data_generation"]:
                return {"eligible": False, "reason": "job_generation_expired"}
            event = row["args"].get("event") or {}
            if event.get("source_invalidated"):
                _, redacted_receipt, _ = event_identity(event)
                progress = await ledger.progress_on(connection, generation, redacted_receipt)
                return {"eligible": False, "reason": "source_withdrawn_consumer_outcome_unknown",
                        "outcome": "unknown", "consumer_progress": progress}
            action_id, receipt_id, revision = event_identity(event)
            delivery = await (await connection.execute(
                "SELECT state FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
                (generation, receipt_id))).fetchone()
            previous = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
            progress = await (await connection.execute(
                "SELECT consumer,disposition,rbi_retry_at,now() AS observed_at FROM conversation.result_consumer_progress "
                "WHERE data_generation=%s AND receipt_id=%s", (generation, receipt_id))).fetchall()
            work = await work_on(connection, generation, event, jobs=self.jobs)
            clock = await (await connection.execute("SELECT now() AS observed_at")).fetchone()
        if previous and not repairing:
            if len(progress) != len(ledger.CONSUMERS) or any(part["disposition"] not in ledger.TERMINAL for part in progress):
                return {"eligible": False, "reason": "result_consumer_outcome_unknown", "outcome": "unknown"}
            return {"eligible": False, "reason": "result_already_consumed"}
        runnable, blocked = runnable_consumers(progress)
        expected = {"data_generation": generation, "action_id": action_id, "receipt_id": receipt_id, "result_revision": revision}
        if any(target.get(key) is not None and target[key] != value for key, value in expected.items()):
            return {"eligible": False, "reason": "original_result_changed"}
        if ((target.get("material_id") and target["material_id"] != receipt_id)
                or (target.get("version") and target["version"] != str(row["id"]))):
            return {"eligible": False, "reason": "original_result_changed"}
        context = context_from_record(event["context"])
        await self.identity.check(context, event_refs(event))
        if self.actions is None:
            return {"eligible": False, "reason": "action_head_unavailable"}
        current = await self._head(event, context)
        budget = await self._budget_for(generation, event, repair_ref=repair_ref, repair_consumer=repair_consumer)
        diagnostics = eligibility(budget, repair_ref=repair_ref, consumer=repair_consumer)
        target_state = None
        if delivery and delivery["state"] != "pending":
            diagnostics.update(eligible=False, reason="result_consumer_outcome_unknown")
        if diagnostics["eligible"] and not repairing and blocked and not runnable and not waiting_consumers(progress):
            diagnostics.update(eligible=False, reason="result_consumer_blocked")
        if diagnostics["eligible"]:
            queued = [job for job in work["jobs"] if job["status"] == "todo"]
            doing = any(job["status"] == "doing" for job in work["jobs"]) or bool(work["running"])
            due = (min(job["scheduled_at"] for job in queued)
                   if queued and not doing and all(job["scheduled_at"] and job["scheduled_at"] > clock["observed_at"]
                                                   for job in queued) else None)
            if repairing:
                part = next((item for item in progress if item["consumer"] == repair_consumer), None)
                if part and part.get("rbi_retry_at") and part["rbi_retry_at"] > part["observed_at"]:
                    due = max(value for value in (due, part["rbi_retry_at"]) if value is not None)
            if not due and not runnable and waiting_consumers(progress):
                due = min(item["rbi_retry_at"] for item in waiting_consumers(progress))
            if due:
                diagnostics.update(eligible=False, reason="result_dependency_wait")
                selected = diagnostics["repair_eligibility"] if repairing else diagnostics["normal_eligibility"]
                selected.update(eligible=False, reason="result_dependency_wait", retry_at=due)
        if repairing:
            refs = unique_refs(event_refs(event), source_refs(current["action"].get("source_refs", ())),
                               result_sources(receipt(current["receipt"])))
            async with self.database.transaction() as connection:
                await self._fence_original_on(connection, row["id"], generation, event, signature(event), context, current, refs)
                evidence = await evidence_on(connection, generation, event, repair_consumer, repair_ref)
                target_state = (await self._target_on(connection, generation, event, context, current,
                                                      repair_consumer, evidence=evidence)
                                if current["result_revision"] == revision else
                                {"state": "superseded", "reason": "result_head_advanced"})
            reason = ("result_consumer_outcome_unknown" if delivery and delivery["state"] != "pending" and not previous else
                      "result_head_advanced" if current["result_revision"] > revision else
                      "result_consumer_proof_missing" if previous and target_state["state"] != "verified" else
                      target_state.get("reason") if target_state["state"] != "not_applied" else
                      "repair_evidence_missing" if not evidence else
                      "repair_failure_history_changed" if evidence["failure_digest"] != budget.get("failure_digest")
                      else None)
            if reason:
                diagnostics.update(eligible=False, reason=reason)
                diagnostics["repair_eligibility"] = {**diagnostics["repair_eligibility"],
                                                       "eligible": False, "reason": reason}
        return {**diagnostics, "version": str(row["id"]), "material_id": receipt_id,
                "material_digest": signature(event), "dependency": "conversation.result",
                "original_state": str(row["status"]), "head_revision": current["result_revision"],
                "repair_ref": repair_ref, "repair_consumer": repair_consumer,
                "target_state": target_state, **expected}

    async def authorize_local_repair(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                                     repair_ref, repair_revision, defect_key, repair_consumer,
                                     defect_evidence_ref, material_digest=None):
        for _ in range(3):
            try:
                return await self._authorize_local_repair_once(job_id, data_generation=data_generation,
                    action_id=action_id, receipt_id=receipt_id, result_revision=result_revision,
                    repair_ref=repair_ref, repair_revision=repair_revision, defect_key=defect_key,
                    repair_consumer=repair_consumer, defect_evidence_ref=defect_evidence_ref,
                    material_digest=material_digest)
            except AppError as error:
                if error.code != "result_head_advanced":
                    raise
        return {"state": "waiting", "reason": "result_head_changing"}

    async def _authorize_local_repair_once(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                                           repair_ref, repair_revision, defect_key, repair_consumer,
                                           defect_evidence_ref, material_digest=None):
        """Trusted coordinator entry: bind a verified defect to the original target."""
        from .rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, execution_identity

        if not validate_repair(repair_ref, repair_consumer):
            raise AppError("invalid_result_repair_target", status=400)
        for value in (repair_revision, defect_key, defect_evidence_ref):
            if (not isinstance(value, str) or not value.strip() or len(value) > 256
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)):
                raise AppError("result_repair_evidence_invalid", status=400)
        event, digest, context, current, refs = await self._prepare_original(job_id,
            data_generation=data_generation, action_id=action_id, receipt_id=receipt_id,
            result_revision=result_revision, material_digest=material_digest)
        async with self.database.transaction() as connection:
            await self._fence_original_on(connection, job_id, data_generation, event, digest, context, current, refs)
            if current["result_revision"] > result_revision:
                return await self._handoff_head_on(connection, data_generation, event, context, current,
                                                   repair_ref=repair_ref, repair_consumer=repair_consumer)
            existing = await evidence_on(connection, data_generation, event, repair_consumer, repair_ref)
            target = await self._target_on(connection, data_generation, event, context, current,
                                           repair_consumer, evidence=existing)
            if target["state"] != "not_applied":
                result = await self._terminal_target_on(connection, data_generation, event, repair_consumer,
                                                        target, evidence=existing)
                if result.get("state") == "verified":
                    await ledger.finish_on(connection, data_generation, receipt_id)
                return result
            delivery = await (await connection.execute(
                "SELECT * FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s FOR UPDATE",
                (data_generation, receipt_id))).fetchone()
            budget = await execution_budget_on(connection, self.jobs, data_generation, event, delivery,
                repair_ref=repair_ref, consumer=repair_consumer, reconcile_stranded=True)
            diagnostics = eligibility(budget, repair_ref=repair_ref, consumer=repair_consumer)
            if (not budget.get("history_complete") or budget.get("unknown_held") or budget.get("active_executions")
                    or budget.get("failed_executions") is None or budget["failed_executions"] < MAX_FAILURES
                    or not budget.get("failure_digest")):
                reason = budget.get("reason") or "normal_failure_budget_not_exhausted"
                return {**diagnostics, "state": "waiting" if reason == "execution_in_progress" else "blocked",
                        "reason": reason}
            grant = await self.jobs.authorize_execution_repair_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(data_generation, event), data_generation=data_generation,
                max_failures=MAX_FAILURES, repair_ref=repair_ref, repair_revision=repair_revision,
                defect_key=defect_key, failure_digest=budget["failure_digest"], consumer=repair_consumer)
            if not grant.get("eligible"):
                return {**diagnostics, "state": "blocked", "reason": grant.get("reason") or "repair_not_authorized",
                        "repair": grant}
            owner_id = target["owner_id"]
            await connection.execute(
                "INSERT INTO conversation.rbj_result_repair_evidence "
                "(data_generation,action_id,receipt_id,result_revision,consumer,defect_key,repair_ref,repair_revision,"
                "defect_evidence_ref,owner_id,owner_revision,material_digest,failure_digest) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (data_generation, action_id, receipt_id, result_revision, repair_consumer, defect_key, repair_ref,
                 repair_revision, defect_evidence_ref, owner_id, target.get("owner_revision"), digest, budget["failure_digest"]))
            saved = await evidence_on(connection, data_generation, event, repair_consumer, repair_ref)
            expected = {"defect_key": defect_key, "repair_revision": repair_revision,
                        "defect_evidence_ref": defect_evidence_ref, "owner_id": owner_id,
                        "failure_digest": budget["failure_digest"]}
            if not saved or any(saved[key] != value for key, value in expected.items()):
                raise AppError("result_repair_evidence_conflict", status=409)
            return {"state": "accepted", "reason": "repair_authorized", "repair": grant,
                    "owner_id": saved["owner_id"], "owner_revision": saved["owner_revision"],
                    "material_digest": digest, "failure_digest": saved["failure_digest"],
                    "normal_eligibility": diagnostics["normal_eligibility"]}

    async def recover_result(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                             attempt_id=None, owner_token=None, material_digest=None, known_job_ids=(),
                             repair_ref=None, repair_consumer=None):
        for _ in range(3):
            try:
                return await self._recover_result_once(job_id, data_generation=data_generation,
                    action_id=action_id, receipt_id=receipt_id, result_revision=result_revision,
                    attempt_id=attempt_id, owner_token=owner_token, material_digest=material_digest,
                    known_job_ids=known_job_ids, repair_ref=repair_ref, repair_consumer=repair_consumer)
            except AppError as error:
                if error.code != "result_head_advanced":
                    raise
        return {"state": "waiting", "reason": "result_head_changing"}

    async def _recover_result_once(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                                   attempt_id=None, owner_token=None, material_digest=None, known_job_ids=(),
                                   repair_ref=None, repair_consumer=None):
        repairing = validate_repair(repair_ref, repair_consumer)
        event, digest, context, current, refs = await self._prepare_original(job_id,
            data_generation=data_generation, action_id=action_id, receipt_id=receipt_id,
            result_revision=result_revision, material_digest=material_digest)
        async with self.database.transaction() as connection:
            await self._fence_original_on(connection, job_id, data_generation, event, digest, context, current, refs)
            if attempt_id is not None:
                if self.guard_on is None:
                    raise AppError("recovery_guard_unavailable", status=503)
                await self.guard_on(connection, attempt_id, owner_token)
            if current["result_revision"] > result_revision:
                return await self._handoff_head_on(connection, data_generation, event, context, current,
                                                   repair_ref=repair_ref, repair_consumer=repair_consumer)
            if repairing:
                evidence = await evidence_on(connection, data_generation, event, repair_consumer, repair_ref)
                target = await self._target_on(connection, data_generation, event, context, current,
                                               repair_consumer, evidence=evidence)
                if target["state"] != "not_applied":
                    result = await self._terminal_target_on(connection, data_generation, event, repair_consumer,
                                                            target, evidence=evidence)
                    if result.get("state") == "verified":
                        await ledger.finish_on(connection, data_generation, receipt_id)
                    return result
                if not evidence:
                    return {"state": "blocked", "reason": "repair_evidence_missing"}
            result = await ensure_on(connection, self.jobs, data_generation, event,
                known_keys=("result-recovery:" + attempt_id,) if attempt_id else (),
                known_job_ids=known_job_ids, reconcile_stranded=True,
                repair_ref=repair_ref, repair_consumer=repair_consumer)
            if result.get("budget"):
                diagnostics = eligibility(result["budget"], repair_ref=repair_ref, consumer=repair_consumer)
                result.update(normal_eligibility=diagnostics["normal_eligibility"],
                              repair_eligibility=diagnostics["repair_eligibility"])
            return result

    async def invoke(self, recovery, target, facts, attempt_id):
        result = await self.recover_result(int(facts["version"]), data_generation=facts["data_generation"],
            action_id=facts["action_id"], receipt_id=facts["receipt_id"], result_revision=facts["result_revision"],
            attempt_id=attempt_id, owner_token=recovery["owner_token"], material_digest=facts["material_digest"],
            repair_ref=facts.get("repair_ref"), repair_consumer=facts.get("repair_consumer"))
        if result["state"] == "verified":
            return {**result, "state": "accepted"}
        if result["state"] == "waiting":
            return {**result, "state": "accepted", "dependency_state": "waiting"}
        if result["state"] == "blocked":
            uncertain = result.get("outcome") == "unknown" or result.get("reason") in {
                "history_unknown", "execution_outcome_unknown", "repair_outcome_unknown"}
            return {**result, "state": "unknown" if uncertain else "failed"}
        return result

    async def observe(self, _recovery, _target, facts, _attempt_id):
        repair_ref, repair_consumer = facts.get("repair_ref"), facts.get("repair_consumer")
        repairing = validate_repair(repair_ref, repair_consumer)
        async with self.database.transaction(read_only=True) as connection:
            generation = await self.jobs.current_generation_on(connection)
            done = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (facts["receipt_id"],))).fetchone()
            row = await (await connection.execute(
                "SELECT state,reason,rbi_reconcile_reason,rbi_reconcile_due_at,now() AS observed_at "
                "FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
                (facts["data_generation"], facts["receipt_id"]))).fetchone()
            original = await self._original_on(connection, {"id": facts["version"]})
        if generation != facts["data_generation"]:
            return {"state": "failed", "reason": "job_generation_expired"}
        if not original or original["cancelled_at"] or original["data_generation"] != generation:
            return {"state": "failed", "reason": "original_result_job_unavailable"}
        event = original["args"].get("event") or {}
        if event.get("source_invalidated"):
            async with self.database.transaction(read_only=True) as connection:
                progress = await ledger.progress_on(connection, generation, facts["receipt_id"])
            return {"state": "unknown", "reason": "source_withdrawn_consumer_outcome_unknown",
                    "disposition": row["state"] if row else None, "consumer_progress": progress}
        try:
            identity = event_identity(event)
            if identity != (facts["action_id"], facts["receipt_id"], facts["result_revision"]):
                return {"state": "failed", "reason": "original_result_changed"}
            if facts.get("material_digest") and signature(event) != facts["material_digest"]:
                return {"state": "failed", "reason": "original_result_changed"}
            context = context_from_record(event["context"])
            await self.identity.check(context, event_refs(event))
            current = await self._head(event, context)
        except AppError as error:
            state = "unknown" if error.status >= 500 else "failed"
            return {"state": state, "reason": error.code}
        async with self.database.transaction(read_only=True) as connection:
            work = await work_on(connection, generation, event, jobs=self.jobs)
            evidence = (await evidence_on(connection, generation, event, repair_consumer, repair_ref)
                        if repairing else None)
        budget = await self._budget_for(generation, event, repair_ref=repair_ref, repair_consumer=repair_consumer)
        diagnostics = eligibility(budget, repair_ref=repair_ref, consumer=repair_consumer)
        if repairing:
            part = next((item for item in work["progress"] if item["consumer"] == repair_consumer), None)
            if part and part["disposition"] in ledger.TERMINAL:
                if part["disposition"] == "done" and not matching_life_proof(event, repair_consumer, part,
                    generation=generation, owner_id=evidence["owner_id"] if evidence else None):
                    return {**diagnostics, "state": "unknown", "reason": "result_consumer_proof_incomplete",
                            "consumer": repair_consumer, "receipt_id": facts["receipt_id"]}
                if part["disposition"] == "not_applicable" and evidence:
                    return {**diagnostics, "state": "unknown", "reason": "result_owner_disposition_conflict",
                            "consumer": repair_consumer, "receipt_id": facts["receipt_id"]}
                if part["disposition"] != "done" and (budget.get("unknown_held") or budget.get("active_executions")):
                    return {**diagnostics, "state": "unknown", "reason": "execution_outcome_unknown",
                            "disposition": part["disposition"], "consumer": repair_consumer,
                            "receipt_id": facts["receipt_id"]}
                return {**diagnostics, "state": "verified", "reason": part["disposition"],
                        "consumer": repair_consumer, "proof": part["proof"], "receipt_id": facts["receipt_id"]}
        if done:
            if repairing:
                return {**diagnostics, "state": "unknown", "reason": "result_consumer_proof_missing",
                        "consumer": repair_consumer, "receipt_id": facts["receipt_id"]}
            if (len(work["progress"]) != len(ledger.CONSUMERS)
                    or any(part["disposition"] not in ledger.TERMINAL for part in work["progress"])):
                return {**diagnostics, "state": "unknown", "reason": "result_consumer_outcome_unknown",
                        "receipt_id": facts["receipt_id"]}
            return {**diagnostics, "state": "verified", "receipt_id": facts["receipt_id"],
                    "disposition": row["state"] if row else "legacy_completed",
                    "head_revision": current["result_revision"]}
        if current["result_revision"] > facts["result_revision"]:
            if row and row["state"] == "superseded" and not done:
                return {**diagnostics, "state": "unknown", "reason": "result_consumer_outcome_unknown",
                        "head_revision": current["result_revision"], "disposition": row["state"]}
            return {"state": "failed", "reason": "result_head_advanced",
                    "head_revision": current["result_revision"]}
        if row and row["state"] != "pending":
            return {"state": "failed", "reason": row["reason"] or "result_terminal_proof_missing",
                    "disposition": row["state"]}
        progress = {item["consumer"]: item["disposition"] for item in work["progress"]}
        if repairing and not evidence:
            return {**diagnostics, "state": "failed", "reason": "repair_evidence_missing", "progress": progress}
        if repairing and diagnostics["eligible"]:
            checked = await self.inspect(None, {"id": facts["version"], "material_id": facts["receipt_id"],
                "version": facts["version"], "repair_ref": repair_ref, "repair_consumer": repair_consumer})
            if not checked.get("eligible"):
                domain = checked.get("target_state") or {}
                domain_state = domain.get("state")
                state = ("unknown" if domain_state in {"unknown", "blocked"} else
                         "pending" if domain_state in {"waiting", "verified", "superseded"} else "failed")
                return {**diagnostics, "state": state, "reason": checked.get("reason") or "repair_unconfirmed",
                        "due_at": domain.get("due_at"), "progress": progress}
        if not diagnostics["eligible"]:
            selected = diagnostics["repair_eligibility"] if repairing else diagnostics["normal_eligibility"]
            reason = diagnostics["reason"]
            state = ("pending" if reason in {"execution_wait", "execution_in_progress", "repair_wait"} else
                     "unknown" if reason in {"history_unknown", "execution_outcome_unknown", "repair_outcome_unknown"} else
                     "failed")
            return {**diagnostics, "state": state, "reason": reason, "progress": progress,
                    "due_at": selected.get("retry_at")}
        if work["running"]:
            return {**diagnostics, "state": "pending", "reason": "consumer_running", "progress": progress,
                    "due_at": min(item["lease_until"] for item in work["running"])}
        if work["jobs"]:
            job = work["jobs"][0]
            reason = "result_job_executing" if job["status"] == "doing" else "result_job_queued"
            return {**diagnostics, "state": "pending", "reason": reason, "job_id": job["id"],
                    "due_at": job["scheduled_at"], "progress": progress}
        if (row and row["rbi_reconcile_due_at"] and row["rbi_reconcile_due_at"] > row["observed_at"]
                and (row["rbi_reconcile_reason"] == "execution_wait" or work["waiting"])):
            return {**diagnostics, "state": "pending", "reason": "result_dependency_wait",
                    "due_at": row["rbi_reconcile_due_at"], "progress": progress}
        if work["blocked"] and not work["runnable"]:
            if not work["waiting"]:
                return {**diagnostics, "state": "failed", "reason": "result_consumer_blocked",
                        "blocked_consumers": work["blocked"], "progress": progress}
        return {**diagnostics, "state": "failed", "reason": "result_execution_missing", "progress": progress,
                "runnable_consumers": work["runnable"], "blocked_consumers": work["blocked"],
                "retry_due_at": min((item["rbi_retry_at"] for item in work["waiting"]), default=None)}

    async def _discover_legacy(self, *, limit=16):
        """Register a bounded page of old result jobs lacking a delivery row."""
        async with self.database.transaction() as connection:
            generation = await self.jobs.current_generation_on(connection)
            await self.jobs.assert_generation_on(connection, generation)
            await connection.execute(
                "INSERT INTO conversation.rbi_result_legacy_scan(data_generation) VALUES(%s) ON CONFLICT DO NOTHING",
                (generation,))
            state = await (await connection.execute(
                "SELECT cursor_job_id,upper_job_id FROM conversation.rbi_result_legacy_scan "
                "WHERE data_generation=%s FOR UPDATE", (generation,))).fetchone()
            upper = state["upper_job_id"]
            if upper is None:
                last = await (await connection.execute(
                    "SELECT j.id FROM procrastinate_jobs j WHERE j.task_name='conversation.result' "
                    "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
                    "AND k.data_generation=%s) ORDER BY j.id DESC LIMIT 1", (generation,))).fetchone()
                upper = max(state["cursor_job_id"], last["id"] if last else 0)
            rows = await (await connection.execute(
                "SELECT j.id,j.args FROM procrastinate_jobs j WHERE j.task_name='conversation.result' "
                "AND j.id>%s AND j.id<=%s AND EXISTS (SELECT 1 FROM runtime.job_keys k "
                "WHERE k.job_id=j.id AND k.data_generation=%s) ORDER BY j.id LIMIT %s",
                (state["cursor_job_id"], upper, generation, limit))).fetchall()
            registered = 0
            for row in rows:
                event = (row["args"] or {}).get("event") or {}
                if not isinstance(event, dict) or event.get("source_invalidated"):
                    continue
                try:
                    _, receipt_id, _ = event_identity(event)
                except AppError:
                    continue
                existing = await (await connection.execute(
                    "SELECT 1 FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s "
                    "UNION ALL SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s LIMIT 1",
                    (generation, receipt_id, receipt_id))).fetchone()
                if existing:
                    continue
                original = await (await connection.execute(
                    "SELECT job_id FROM runtime.job_keys WHERE key=%s AND task='conversation.result' "
                    "AND data_generation=%s AND cancelled_at IS NULL",
                    ("capability.result:" + receipt_id, generation))).fetchone()
                await ledger.register_on(connection, generation, event, original["job_id"] if original else None)
                registered += 1
            cursor = rows[-1]["id"] if rows else upper
            finished = len(rows) < limit
            await connection.execute(
                "UPDATE conversation.rbi_result_legacy_scan SET cursor_job_id=%s,upper_job_id=%s,updated_at=now() "
                "WHERE data_generation=%s", (upper if finished else cursor, None if finished else upper, generation))
            return {"scanned": len(rows), "registered": registered, "page_complete": finished}

    async def reconcile_due(self, *, limit=32):
        """Bounded foreground metadata scan; it only arranges native result jobs."""
        if self.actions is None:
            return {"scanned": 0, "woken": 0, "complete": False, "reason": "action_head_unavailable"}
        limit = max(1, min(int(limit), 128))
        deadline = asyncio.get_running_loop().time() + 5.0
        scanned = woken = 0
        async with asyncio.timeout(max(0.01, deadline - asyncio.get_running_loop().time())):
            legacy = await self._discover_legacy()
        while scanned < limit and asyncio.get_running_loop().time() < deadline:
            async with self.database.transaction() as connection:
                generation = await self.jobs.current_generation_on(connection)
                await self.jobs.assert_generation_on(connection, generation)
                row = await (await connection.execute(
                    "SELECT data_generation,receipt_id,action_id,result_revision,original_job_id "
                    "FROM conversation.result_deliveries WHERE data_generation=%s AND state='pending' "
                    "AND rbi_reconcile_due_at<=now() ORDER BY rbi_reconcile_due_at,receipt_id "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED", (generation,))).fetchone()
                if row:
                    await connection.execute(
                        "UPDATE conversation.result_deliveries SET rbi_reconcile_checked_at=now(),"
                        "rbi_reconcile_due_at=now()+interval '30 seconds' "
                        "WHERE data_generation=%s AND receipt_id=%s", (generation, row["receipt_id"]))
            if not row:
                break
            scanned += 1
            reason, due_at, stop = None, None, False
            if row["original_job_id"] is None:
                reason, stop = "original_result_job_unavailable", True
            else:
                try:
                    async with asyncio.timeout(max(0.01, deadline - asyncio.get_running_loop().time())):
                        result = await self.recover_result(
                            row["original_job_id"], data_generation=generation,
                            action_id=row["action_id"], receipt_id=row["receipt_id"],
                            result_revision=row["result_revision"])
                    reason = result.get("reason")
                    due_at = result.get("due_at")
                    if result.get("state") == "accepted" and reason == "scheduled":
                        woken += 1
                    stop = (result.get("state") == "blocked" and reason not in {
                        "execution_in_progress", "execution_outcome_unknown", "history_unknown"})
                except AppError as error:
                    reason = error.code
                    stop = error.code in {
                        "original_result_job_unavailable", "original_result_changed", "source_withdrawn",
                        "source_unavailable", "source_revoked", "source_suppressed", "access_changed",
                        "forbidden", "job_generation_expired", "result_receipt_revision_conflict",
                        "result_consumer_blocked", "result_head_regressed", "result_already_consumed",
                    }
                except TimeoutError:
                    reason = "result_reconcile_timeout"
                except Exception as error:
                    # No original payload or exception text is included in the scan record.
                    reason = "result_reconcile_" + type(error).__name__[:64]
            async with self.database.transaction() as connection:
                await connection.execute(
                    "UPDATE conversation.result_deliveries SET rbi_reconcile_reason=%s,"
                    "rbi_reconcile_due_at=CASE WHEN %s THEN NULL WHEN %s::timestamptz IS NULL OR %s<=now() "
                    "THEN now()+interval '30 seconds' ELSE %s END "
                    "WHERE data_generation=%s AND receipt_id=%s AND state='pending'",
                    (reason, stop, due_at, due_at, due_at, generation, row["receipt_id"]))
        async with self.database.transaction(read_only=True) as connection:
            generation = await self.jobs.current_generation_on(connection)
            remaining = await (await connection.execute(
                "SELECT 1 FROM conversation.result_deliveries WHERE data_generation=%s AND state='pending' "
                "AND rbi_reconcile_due_at<=now() LIMIT 1", (generation,))).fetchone()
        return {"scanned": scanned, "woken": woken, "legacy": legacy, "complete": not bool(remaining),
                "complete_scope": "currently_due_in_current_generation"}

    def registration(self):
        return {"name": "conversation.result.recover", "error_families": ("result_consumption_incomplete",),
                "inspect": self.inspect, "invoke": self.invoke, "observe": self.observe,
                "max_attempts": 3, "observation_only": True}
