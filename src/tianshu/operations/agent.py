"""Independent low-frequency operations loop, consuming the public log port."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import json

from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from .incidents import identifier


class OperationsAgent:
    def __init__(self, database, diagnostics, incidents, executor, diagnosis, outbox, *, progress=None, host_spool=None):
        self.database, self.diagnostics, self.incidents = database, diagnostics, incidents
        self.executor, self.diagnosis, self.outbox, self.progress = executor, diagnosis, outbox, progress
        self.host_spool = host_spool
        self.task = None
        self.last_error = None
        self.last_tick_at = None

    async def poll_events(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (await conn.execute("SELECT checkpoint FROM operations.consumer_checkpoints "
                                           "WHERE consumer='runtime_logs'")).fetchone()
        checkpoint = row["checkpoint"] if row else None
        page = await self.diagnostics.log_changes(checkpoint=checkpoint, limit=500)
        for event in page.get("entries", ()):
            if not isinstance(event, dict) or event.get("role") == "operations":
                continue
            if event.get("outcome") not in {"failed", "degraded"} and event.get("level") not in {"ERROR", "CRITICAL", "FATAL"}:
                continue
            try:
                event_id = identifier(event.get("event_id"))
            except AppError:
                continue
            dependency = identifier(event.get("dependency_ref") or event.get("task_name") or event.get("role"), fallback="runtime")
            family = identifier(event.get("error_family") or event.get("error_code"), fallback="unknown")
            role = identifier(event.get("role"), fallback="runtime")
            incident = await self.incidents.ingest({**event, "event_id": event_id, "dependency": dependency,
                                         "error_family": family, "version": identifier(event.get("version"), fallback="unknown"),
                                         "affected": [role], "severity": "critical" if event.get("level") in {"CRITICAL", "FATAL"} else "error"})
            await self.outbox.enqueue(incident, "opened")
            if incident["state"] == "escalated" or incident.get("severity_increased"):
                await self.outbox.enqueue(incident, "escalated")
        # Individual inserts precede this checkpoint. Crash before checkpoint
        # only replays the same event IDs, and cannot lose an incident.
        async with self.database.transaction(control=True) as conn:
            await conn.execute("INSERT INTO operations.consumer_checkpoints(consumer,checkpoint,coverage,freshness) "
                "VALUES('runtime_logs',%s,%s,%s) ON CONFLICT(consumer) DO UPDATE SET checkpoint=excluded.checkpoint,"
                "coverage=excluded.coverage,freshness=excluded.freshness,updated_at=now()",
                (page.get("next_checkpoint"), Jsonb(page.get("coverage", {})), Jsonb(page.get("freshness", {}))))

    async def poll_progress(self):
        if self.progress is None:
            return
        now = datetime.now(UTC)
        for fact in await self.progress():
            if not isinstance(fact, dict) or fact.get("eligible") is not True:
                continue
            if any(fact.get(key) for key in ("paused", "cancelled", "executing", "waiting_dependency")):
                continue
            due = fact.get("due_at")
            if not isinstance(due, datetime) or due.tzinfo is None or due > now-timedelta(minutes=2):
                continue
            if fact.get("version") is None:
                continue
            dependency = identifier(fact.get("dependency"))
            material = identifier(fact.get("material_id"))
            version = identifier(str(fact.get("version")))
            code = hashlib.sha256(json.dumps([dependency, material, version]).encode()).hexdigest()
            incident = await self.incidents.ingest({"event_id": "no-progress:"+code, "dependency": dependency,
                "error_family": identifier(fact.get("error_family"), fallback="eligible_no_progress"), "version": version,
                "material_id": material,
                "recovery_target": fact.get("target") or {"material_id": material},
                "root_incident_id": fact.get("root_incident_id"),
                "affected": [identifier(fact.get("role"), fallback="runtime")],
                "progress": {"eligible": True, "due": "overdue", "version": version}, "severity": "warning"})
            await self.outbox.enqueue(incident, "opened")
            if incident["state"] == "escalated":
                await self.outbox.enqueue(incident, "escalated")

    async def poll_model_reloads(self):
        # Configuration changes are durably recorded by Models even if the
        # original broker reload could not be completed.
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (await conn.execute("SELECT checkpoint,coverage FROM operations.consumer_checkpoints "
                                           "WHERE consumer='models_configuration_reloads'")).fetchone()
        checkpoint = row["checkpoint"] if row else None
        try:
            cursor = json.loads(checkpoint) if checkpoint else None
        except (TypeError, ValueError):
            cursor = None
        page = await self.diagnosis.models.pending_configuration_reloads(limit=100, cursor=cursor)
        if (not isinstance(page, dict) or not isinstance(page.get("items"), list)
                or "next_cursor" not in page
                or page["next_cursor"] is not None and not isinstance(page["next_cursor"], dict)):
            raise AppError("invalid_model_reload_page")
        previous = row["coverage"] if row and cursor is not None else {}
        processed = previous.get("processed", 0)
        failed = previous.get("failed", 0)
        invalid = previous.get("invalid", 0)
        for pending in page["items"]:
            if not isinstance(pending, dict):
                invalid += 1
                continue
            event_id, revision = pending.get("event_id"), pending.get("revision")
            if (not isinstance(event_id, str) or len(event_id) > 170
                    or type(revision) is not int or revision < 1):
                invalid += 1
                continue
            try:
                identifier(event_id)
            except AppError:
                invalid += 1
                continue
            try:
                incident = await self.incidents.ingest({
                    "event_id": "ops-model-reload:" + event_id,
                    "dependency": "model-broker", "error_family": "broker_reload_pending",
                    "version": str(revision), "material_id": event_id,
                    "recovery_target": {"material_id": event_id, "version": str(revision)},
                    "affected": ["model-broker"], "severity": "error"})
                await self.outbox.enqueue(incident, "opened")
            except Exception:
                # The original record remains pending in Models. Continue this
                # sweep; the next sweep will revisit it without losing identity.
                failed += 1
                continue
            processed += 1
        next_cursor = page["next_cursor"]
        serialized = json.dumps(next_cursor, ensure_ascii=False, separators=(",", ":")) if next_cursor is not None else None
        coverage = {"scope": "finite_sweep", "processed": processed, "failed": failed, "invalid": invalid,
                    "complete": next_cursor is None and failed == 0 and invalid == 0}
        async with self.database.transaction(control=True) as conn:
            await conn.execute("INSERT INTO operations.consumer_checkpoints(consumer,checkpoint,coverage,freshness) "
                "VALUES('models_configuration_reloads',%s,%s,%s) ON CONFLICT(consumer) DO UPDATE SET "
                "checkpoint=excluded.checkpoint,coverage=excluded.coverage,freshness=excluded.freshness,updated_at=now() "
                "WHERE operations.consumer_checkpoints.checkpoint IS NOT DISTINCT FROM %s",
                (serialized, Jsonb(coverage), Jsonb({"as_of": datetime.now(UTC).isoformat()}), checkpoint))

    async def process_open(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            incidents = await (await conn.execute("SELECT * FROM operations.incidents "
                "WHERE state IN ('open','observing') AND diagnostic_state='pending' "
                "AND NOT EXISTS (SELECT 1 FROM operations.recoveries r WHERE r.incident_id=operations.incidents.incident_id "
                "AND r.state IN ('requested','inspecting','executing','observing')) "
                "ORDER BY first_seen LIMIT 32")).fetchall()
        for incident in incidents:
            await self.outbox.enqueue(incident, "opened")
            if incident["diagnostic_state"] != "pending":
                continue
            async with self.database.transaction(control=True, read_only=True) as conn:
                recoveries = await (await conn.execute("SELECT action,state,target_id FROM operations.recoveries "
                    "WHERE incident_id=%s AND incident_revision=%s ORDER BY created_at",
                    (incident["incident_id"], incident["revision"]))).fetchall()
                targets = await (await conn.execute("SELECT target_id,target,state FROM operations.incident_targets "
                    "WHERE incident_id=%s AND episode=%s ORDER BY target_id", (incident["incident_id"], incident["episode"]))).fetchall()
            if any(row["state"] in {"requested", "inspecting", "executing", "observing"} for row in recoveries):
                continue
            options = self.executor.applicable(incident)
            if targets and incident["unscoped_occurrences"] == 0 and all(target["state"] == "verified" for target in targets):
                async with self.database.transaction(control=True) as conn:
                    resolved = await (await conn.execute("UPDATE operations.incidents i SET state='resolved',resolved_at=now(),revision=revision+1 "
                        "WHERE i.incident_id=%s AND i.revision=%s AND i.unscoped_occurrences=0 AND NOT EXISTS "
                        "(SELECT 1 FROM operations.incident_targets t WHERE t.incident_id=i.incident_id AND t.episode=i.episode "
                        "AND t.state<>'verified') RETURNING i.*", (incident["incident_id"], incident["revision"]))).fetchone()
                if resolved:
                    await self.outbox.enqueue(resolved, "resolved")
                continue
            if options:
                action = self.executor.actions[options[0]]
                pending_targets = [target for target in targets if target["state"] == "open" and not any(
                    recovery["target_id"] == target["target_id"] and recovery["action"] == action.name for recovery in recoveries)]
                if pending_targets:
                    for target in pending_targets:
                        await self.executor.enqueue_automatic(incident, action.name, target["target"])
                    continue
                if not targets and not recoveries and action.resolves_dependency:
                    await self.executor.enqueue_automatic(incident, action.name)
                    continue
            await self.diagnosis.start(incident)

    async def process_finished_decisions(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            rows = await (await conn.execute(
                "SELECT DISTINCT i.incident_id,i.revision FROM operations.incidents i "
                "JOIN operations.diagnostics d USING(incident_id) JOIN operations.recoveries r USING(incident_id) "
                "WHERE i.state IN ('open','observing') AND i.diagnostic_state='submitted' "
                "AND d.status='succeeded' AND d.incident_revision=i.revision AND r.incident_revision=i.revision "
                "AND (d.decision->>'action'='invoke_registered_recovery' OR "
                "(d.decision->>'action'='observe_result' AND NOT EXISTS "
                "(SELECT 1 FROM operations.recoveries u WHERE u.incident_id=i.incident_id AND u.state='unknown'))) "
                "AND NOT EXISTS (SELECT 1 FROM operations.recoveries a WHERE a.incident_id=i.incident_id "
                "AND a.state IN ('requested','inspecting','executing','observing')) "
                "AND NOT EXISTS (SELECT 1 FROM runtime.recovery_lineages l WHERE l.lineage_id=i.recovery_lineage "
                "AND l.due_at IS NOT NULL) LIMIT 32")).fetchall()
        for row in rows:
            await self.diagnosis.escalate(row["incident_id"], "recovery_not_verified", expected_revision=row["revision"])

    async def tick(self):
        # Each component is isolated: log/notification failure never interrupts
        # the worker or recursively diagnoses its own transport errors.
        operations = ((self.host_spool.import_pending,) if self.host_spool else ()) + (self.poll_events, self.poll_progress, self.poll_model_reloads, self.executor.replay_statistics, self.executor.claim_retries,
                      self.executor.process_requested, self.executor.observe_pending, self.executor.observe_unknown,
                      self.diagnosis.observe, self.process_open, self.process_finished_decisions,
                      self.outbox.release_suppressed, self.outbox.deliver_due)
        for operation in operations:
            try:
                await asyncio.wait_for(operation(), timeout=55)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = {"component": operation.__name__, "type": type(exc).__name__,
                                   "observed_at": datetime.now(UTC).isoformat()}
        self.last_tick_at = datetime.now(UTC)

    def status(self):
        alive = bool(self.task and not self.task.done())
        recent = bool(self.last_tick_at and datetime.now(UTC)-self.last_tick_at < timedelta(minutes=3))
        return {"ready": alive and recent, "loop_alive": alive, "last_tick_at": self.last_tick_at,
                "last_error": self.last_error, "business_progress": "unknown"}

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(30)

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="operations-agent")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
