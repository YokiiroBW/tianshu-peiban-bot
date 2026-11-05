"""Operations can requeue an original result without constructing its consumer."""
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass
from .codec import context_from_record, signature
from .rbg_result_ledger import event_identity
from .rbg_result_withdrawal import event_refs


class ResultRecovery:
    def __init__(self, database, jobs, identity, guard_on=None):
        self.database, self.jobs, self.identity, self.guard_on = database, jobs, identity, guard_on

    async def candidates(self, *, limit=32, cursor=None):
        limit = max(1, min(int(limit), 250))
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "WITH native AS (SELECT j.id,j.status,j.scheduled_at,j.args->'event'->>'action_id' AS action_id,"
                "j.args->'event'->>'event_id' AS receipt_id,j.args->'event'->>'result_revision' AS result_revision,"
                "k.data_generation,k.created_at,d.original_job_id FROM procrastinate_jobs j "
                "JOIN runtime.job_keys k ON k.job_id=j.id "
                "JOIN runtime.installation_generation g ON g.singleton=true AND g.id=k.data_generation "
                "LEFT JOIN conversation.result_consumption c ON c.receipt_id=j.args->'event'->>'event_id' "
                "LEFT JOIN conversation.result_deliveries d ON d.data_generation=k.data_generation "
                "AND d.receipt_id=j.args->'event'->>'event_id' "
                "WHERE j.task_name='conversation.result' AND k.cancelled_at IS NULL AND c.receipt_id IS NULL "
                "AND j.args->'event'->>'type'='capability.result' "
                "AND j.args->'event'->>'event_id' IS NOT NULL "
                "AND COALESCE(j.args->'event'->>'source_invalidated','false')<>'true'), "
                "originals AS (SELECT DISTINCT ON (data_generation,receipt_id) * FROM native "
                "ORDER BY data_generation,receipt_id,CASE WHEN id=original_job_id THEN 0 ELSE 1 END,id), "
                "bounded AS (SELECT * FROM originals WHERE (%s::bigint IS NULL OR id>%s) ORDER BY id LIMIT %s) "
                "SELECT b.*,progress.blocked,progress.live_lease_until,progress.progress_at,"
                "wake.queued,wake.queued_due_at,wake.live_doing,observed.last_event_at FROM bounded b "
                "LEFT JOIN LATERAL (SELECT bool_or(p.disposition='blocked') AS blocked,"
                "max(p.lease_until) FILTER (WHERE p.disposition='running' AND p.lease_until>now()) AS live_lease_until,"
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
                "JOIN runtime.job_keys k ON k.job_id=j.id JOIN procrastinate_events e ON e.job_id=j.id "
                "WHERE k.data_generation=b.data_generation AND j.task_name='conversation.result' "
                "AND j.args->'event'->>'event_id'=b.receipt_id AND j.args->'event'->>'action_id'=b.action_id "
                "AND j.args->'event'->>'result_revision'=b.result_revision) observed ON true "
                "ORDER BY b.id", (cursor, cursor, limit))).fetchall()
        result = []
        for row in rows:
            if not row["action_id"] or not row["receipt_id"] or not (row["result_revision"] or "").isdigit():
                continue
            target = {"kind": "conversation.result", "id": str(row["id"]),
                      "material_id": row["receipt_id"], "version": str(row["id"])}
            last_progress = max(value for value in (row["created_at"], row["progress_at"], row["last_event_at"]) if value)
            due_at = row["queued_due_at"] or row["live_lease_until"] or last_progress
            result.append({"eligible": not row["blocked"], "reason": "result_consumer_blocked" if row["blocked"] else None,
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
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.id=%s AND j.task_name='conversation.result'",
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
            blocked = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumer_progress WHERE data_generation=%s AND receipt_id=%s "
                "AND disposition='blocked' LIMIT 1", (generation, receipt_id))).fetchone()
        if previous:
            return {"eligible": False, "reason": "result_already_consumed"}
        if blocked:
            return {"eligible": False, "reason": "result_consumer_blocked"}
        expected = {"data_generation": generation, "action_id": action_id, "receipt_id": receipt_id, "result_revision": revision}
        if any(target.get(key) is not None and target[key] != value for key, value in expected.items()):
            return {"eligible": False, "reason": "original_result_changed"}
        if ((target.get("material_id") and target["material_id"] != receipt_id)
                or (target.get("version") and target["version"] != str(row["id"]))):
            return {"eligible": False, "reason": "original_result_changed"}
        context = context_from_record(event["context"])
        await self.identity.check(context, event_refs(event))
        return {"eligible": True, "version": str(row["id"]), "material_id": receipt_id,
                "material_digest": signature(event), "dependency": "conversation.result",
                "original_state": str(row["status"]), **expected}

    async def recover_result(self, job_id, *, data_generation, action_id, receipt_id, result_revision,
                             attempt_id=None, owner_token=None, material_digest=None):
        target = {"job_id": job_id, "data_generation": data_generation, "action_id": action_id,
                  "receipt_id": receipt_id, "result_revision": result_revision}
        facts = await self.inspect(None, target)
        if not facts.get("eligible"):
            raise AppError(facts.get("reason") or "result_recovery_ineligible", status=409)
        if material_digest is not None and facts["material_digest"] != material_digest:
            raise AppError("original_result_changed", status=409)
        async with self.database.transaction() as connection:
            row = await self._original_on(connection, target)
            if not row or row["cancelled_at"] or row["data_generation"] != data_generation:
                raise AppError("original_result_changed", status=409)
            event = row["args"].get("event") or {}
            if event.get("source_invalidated"):
                raise AppError("source_withdrawn", status=409)
            if event_identity(event) != (action_id, receipt_id, result_revision) or signature(event) != facts["material_digest"]:
                raise AppError("original_result_changed", status=409)
            context = context_from_record(event["context"])
            if not await self.identity.sources.validate_persistence(connection, context, event_refs(event)):
                raise AppError("source_unavailable", status=403)
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
            previous = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (receipt_id,))).fetchone()
            if previous:
                return {"state": "verified", "reason": "result_already_consumed"}
            progress = await (await connection.execute(
                "SELECT disposition,lease_until,now() AS now FROM conversation.result_consumer_progress "
                "WHERE data_generation=%s AND receipt_id=%s FOR UPDATE", (data_generation, receipt_id))).fetchall()
            if any(item["disposition"] == "blocked" for item in progress):
                raise AppError("result_consumer_blocked", status=409)
            if any(item["disposition"] == "running" and item["lease_until"] and item["lease_until"] > item["now"]
                   for item in progress):
                return {"state": "accepted", "reason": "consumer_running"}
            active = await (await connection.execute(
                "SELECT j.id FROM procrastinate_jobs j JOIN runtime.job_keys k ON k.job_id=j.id "
                "LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
                "WHERE j.task_name='conversation.result' AND (j.status='todo' OR "
                "(j.status='doing' AND w.last_heartbeat>now()-interval '30 seconds')) "
                "AND k.cancelled_at IS NULL AND k.data_generation=%s AND j.args->'event'->>'event_id'=%s "
                "AND j.args->'event'->>'action_id'=%s AND j.args->'event'->>'result_revision'=%s "
                "AND COALESCE(j.args->'event'->>'source_invalidated','false')<>'true' "
                "ORDER BY j.id LIMIT 1", (data_generation, receipt_id, action_id, str(result_revision)))).fetchone()
            if active:
                return {"state": "accepted", "job_id": active["id"], "reason": "already_queued"}
            from uuid import uuid4
            key = "result-recovery:" + (attempt_id or str(uuid4()))
            queued = await self.jobs.enqueue(connection, JobSpec("conversation.result", key, {"event": event},
                ServiceClass.INTERACTIVE, subject_id=event["context"]["actor_id"], data_generation=data_generation))
            return {"state": "accepted", "job_id": queued}

    async def invoke(self, recovery, target, facts, attempt_id):
        return await self.recover_result(int(facts["version"]), data_generation=facts["data_generation"],
            action_id=facts["action_id"], receipt_id=facts["receipt_id"], result_revision=facts["result_revision"],
            attempt_id=attempt_id, owner_token=recovery["owner_token"], material_digest=facts["material_digest"])

    async def observe(self, _recovery, _target, facts, _attempt_id):
        async with self.database.transaction(read_only=True) as connection:
            done = await (await connection.execute(
                "SELECT 1 FROM conversation.result_consumption WHERE receipt_id=%s", (facts["receipt_id"],))).fetchone()
            row = await (await connection.execute(
                "SELECT state,reason FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id=%s",
                (facts["data_generation"], facts["receipt_id"]))).fetchone()
        if done:
            return {"state": "verified", "receipt_id": facts["receipt_id"], "disposition": row["state"] if row else "legacy_completed"}
        return {"state": "pending", "reason": row["reason"] if row else "result_not_consumed"}

    def registration(self):
        return {"name": "conversation.result.recover", "error_families": ("result_consumption_incomplete",),
                "inspect": self.inspect, "invoke": self.invoke, "observe": self.observe,
                "max_attempts": 3, "observation_only": True}
