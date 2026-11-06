"""Observe and wake original result consumers without executing an Action."""
import asyncio

from tianshu.capabilities.actions import result_sources
from tianshu.runtime.errors import AppError
from tianshu.sources.dependencies import unique_refs
from .codec import context_from_record, receipt, signature, source_refs
from . import rbg_result_ledger as ledger
from .rbg_result_ledger import event_identity
from .rbg_result_withdrawal import event_refs
from .rbi_result_wake import ensure_on, runnable_consumers, waiting_consumers, work_on


class ResultRecovery:
    def __init__(self, database, jobs, identity, guard_on=None, *, actions=None):
        self.database, self.jobs, self.identity, self.guard_on = database, jobs, identity, guard_on
        self.actions = actions

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

    async def candidates(self, *, limit=32, cursor=None):
        limit = max(1, min(int(limit), 250))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "WITH bounded AS (SELECT d.original_job_id AS id,j.status,j.scheduled_at,"
                "d.action_id,d.receipt_id,d.result_revision::text AS result_revision,d.data_generation,"
                "COALESCE(k.created_at,d.created_at) AS created_at,d.original_job_id,j.args,k.cancelled_at,"
                "k.created_at AS key_created_at "
                "FROM conversation.result_deliveries d "
                "JOIN runtime.installation_generation g ON g.singleton=true AND g.id=d.data_generation "
                "LEFT JOIN procrastinate_jobs j ON j.id=d.original_job_id AND j.task_name='conversation.result' "
                "LEFT JOIN LATERAL (SELECT created_at,cancelled_at FROM runtime.job_keys k "
                "WHERE k.job_id=d.original_job_id AND k.data_generation=d.data_generation "
                "AND k.task='conversation.result' ORDER BY k.cancelled_at NULLS FIRST,k.created_at LIMIT 1) k ON true "
                "LEFT JOIN conversation.result_consumption c ON c.receipt_id=d.receipt_id "
                "WHERE d.state='pending' AND d.original_job_id IS NOT NULL AND c.receipt_id IS NULL "
                "AND (%s::bigint IS NULL OR d.original_job_id>%s) "
                "ORDER BY CASE WHEN %s::bigint IS NULL THEN d.rbi_reconcile_checked_at END NULLS FIRST,"
                "d.original_job_id LIMIT %s) "
                "SELECT b.*,progress.consumers,progress.live_lease_until,progress.retry_due_at,progress.progress_at,"
                "wake.queued,wake.queued_due_at,wake.live_doing,observed.last_event_at FROM bounded b "
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
            reason = ("original_result_job_unavailable" if missing else "source_withdrawn" if withdrawn else
                      "result_consumer_blocked" if stopped else None)
            result.append({"eligible": reason is None, "reason": reason,
                "due_at": due_at, "dependency": "conversation.result",
                "error_family": "result_consumption_incomplete", "version": str(row["id"]),
                "material_id": row["receipt_id"], "role": "bot-foreground",
                "executing": bool(row["live_doing"] or row["live_lease_until"]),
                "waiting_dependency": bool(row["queued"]), "target": target})
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
        async with self.database.transaction(read_only=True) as connection:
            row = await self._original_on(connection, target)
            if not row or row["cancelled_at"]:
                return {"eligible": False, "reason": "original_result_job_unavailable"}
            generation = await self.jobs.current_generation_on(connection)
            if generation != row["data_generation"]:
                return {"eligible": False, "reason": "job_generation_expired"}
            event = row["args"].get("event") or {}
            if event.get("source_invalidated"):
                return {"eligible": False, "reason": "source_withdrawn"}
            action_id, receipt_id, revision = event_identity(event)
            previous = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
            progress = await (await connection.execute(
                "SELECT consumer,disposition,rbi_retry_at,now() AS observed_at FROM conversation.result_consumer_progress "
                "WHERE data_generation=%s AND receipt_id=%s", (generation, receipt_id))).fetchall()
        if previous:
            return {"eligible": False, "reason": "result_already_consumed"}
        runnable, blocked = runnable_consumers(progress)
        if blocked and not runnable and not waiting_consumers(progress):
            return {"eligible": False, "reason": "result_consumer_blocked"}
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
        return {"eligible": True, "version": str(row["id"]), "material_id": receipt_id,
                "material_digest": signature(event), "dependency": "conversation.result",
                "original_state": str(row["status"]), "head_revision": current["result_revision"], **expected}

    async def recover_result(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                             attempt_id=None, owner_token=None, material_digest=None, known_job_ids=()):
        target = {"job_id": job_id, "data_generation": data_generation, "action_id": action_id,
                  "receipt_id": receipt_id, "result_revision": result_revision}
        facts = await self.inspect(None, target)
        if not facts.get("eligible"):
            raise AppError(facts.get("reason") or "result_recovery_ineligible", status=409)
        if material_digest is not None and facts["material_digest"] != material_digest:
            raise AppError("original_result_changed", status=409)
        async with self.database.transaction(read_only=True) as connection:
            original = await self._original_on(connection, target)
        if not original or original["cancelled_at"] or original["data_generation"] != data_generation:
            raise AppError("original_result_changed", status=409)
        event = original["args"].get("event") or {}
        if event.get("source_invalidated") or signature(event) != facts["material_digest"]:
            raise AppError("original_result_changed", status=409)
        context = context_from_record(event["context"])
        current = await self._head(event, context)
        refs = unique_refs(event_refs(event), source_refs(current["action"].get("source_refs", ())),
                           result_sources(receipt(current["receipt"])))
        async with self.database.transaction() as connection:
            row = await self._original_on(connection, target)
            if not row or row["cancelled_at"] or row["data_generation"] != data_generation:
                raise AppError("original_result_changed", status=409)
            event = row["args"].get("event") or {}
            if event.get("source_invalidated"):
                raise AppError("source_withdrawn", status=409)
            if event_identity(event) != (action_id, receipt_id, result_revision) or signature(event) != facts["material_digest"]:
                raise AppError("original_result_changed", status=409)
            if not await self.identity.sources.validate_persistence(connection, context, refs):
                raise AppError("source_unavailable", status=403)
            await self.actions.assert_result_head_on(connection, context, action_id=action_id,
                expected_receipt=current["receipt"], result_revision=current["result_revision"],
                data_generation=data_generation, source_refs=refs)
            if attempt_id is not None:
                if self.guard_on is None:
                    raise AppError("recovery_guard_unavailable", status=503)
                await self.guard_on(connection, attempt_id, owner_token)
            await self.jobs.assert_generation_on(connection, data_generation)
            await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("conversation.result.recovery:" + data_generation + ":" + receipt_id,))
            row = await self._original_on(connection, target)
            if not row or row["cancelled_at"] or row["data_generation"] != data_generation:
                raise AppError("original_result_changed", status=409)
            event = row["args"].get("event") or {}
            if event_identity(event) != (action_id, receipt_id, result_revision) or signature(event) != facts["material_digest"]:
                raise AppError("original_result_changed", status=409)
            if current["result_revision"] > result_revision:
                await ledger.queue_head_on(connection, self.jobs, data_generation, current)
                await ledger.supersede_on(connection, data_generation, receipt_id)
                return {"state": "verified", "reason": "newer_action_result"}
            return await ensure_on(connection, self.jobs, data_generation, event,
                known_keys=("result-recovery:" + attempt_id,) if attempt_id else (),
                known_job_ids=known_job_ids, reconcile_stranded=True)

    async def invoke(self, recovery, target, facts, attempt_id):
        result = await self.recover_result(int(facts["version"]), data_generation=facts["data_generation"],
            action_id=facts["action_id"], receipt_id=facts["receipt_id"], result_revision=facts["result_revision"],
            attempt_id=attempt_id, owner_token=recovery["owner_token"], material_digest=facts["material_digest"])
        if result["state"] == "verified":
            return {**result, "state": "accepted"}
        if result["state"] == "blocked":
            return {**result, "state": "failed"}
        return result

    async def observe(self, _recovery, _target, facts, _attempt_id):
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
            return {"state": "failed", "reason": "source_withdrawn",
                    "disposition": row["state"] if row else None}
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
        if done:
            return {"state": "verified", "receipt_id": facts["receipt_id"],
                    "disposition": row["state"] if row else "legacy_completed",
                    "head_revision": current["result_revision"]}
        if current["result_revision"] > facts["result_revision"]:
            return {"state": "failed", "reason": "result_head_advanced",
                    "head_revision": current["result_revision"]}
        if row and row["state"] != "pending":
            return {"state": "failed", "reason": row["reason"] or "result_terminal_proof_missing",
                    "disposition": row["state"]}
        async with self.database.transaction(read_only=True) as connection:
            work = await work_on(connection, generation, event)
        progress = {item["consumer"]: item["disposition"] for item in work["progress"]}
        if work["running"]:
            return {"state": "pending", "reason": "consumer_running", "progress": progress,
                    "due_at": min(item["lease_until"] for item in work["running"])}
        if work["jobs"]:
            job = work["jobs"][0]
            reason = "result_job_executing" if job["status"] == "doing" else "result_job_queued"
            return {"state": "pending", "reason": reason, "job_id": job["id"],
                    "due_at": job["scheduled_at"], "progress": progress}
        if row and row["rbi_reconcile_reason"] in {"history_unknown", "execution_outcome_unknown"}:
            return {"state": "unknown", "reason": row["rbi_reconcile_reason"], "progress": progress,
                    "due_at": row["rbi_reconcile_due_at"]}
        if row and row["rbi_reconcile_reason"] == "execution_failure_budget_exhausted":
            return {"state": "failed", "reason": "execution_failure_budget_exhausted", "progress": progress}
        if (row and row["rbi_reconcile_due_at"] and row["rbi_reconcile_due_at"] > row["observed_at"]
                and (row["rbi_reconcile_reason"] == "execution_wait" or work["waiting"])):
            return {"state": "pending", "reason": "result_dependency_wait",
                    "due_at": row["rbi_reconcile_due_at"], "progress": progress}
        if work["blocked"] and not work["runnable"]:
            if not work["waiting"]:
                return {"state": "failed", "reason": "result_consumer_blocked",
                        "blocked_consumers": work["blocked"], "progress": progress}
        return {"state": "failed", "reason": "result_execution_missing", "progress": progress,
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
