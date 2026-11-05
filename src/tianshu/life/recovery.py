"""Rebuild native jobs from current Life state; never reissue unknown business work."""
from datetime import UTC, datetime
from uuid import uuid4

from tianshu.conversation.codec import context_from_record, source_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from .continuation import action_resolved, resolved_read_failure


async def scheduled_context(life, value, *, connection=None):
    original = context_from_record(value) if isinstance(value, dict) else value
    return await life.identity.refresh_scheduled_context(original, permission="life.manage", connection=connection)


async def recover(worker):
    life = worker.life
    async with life.database.transaction(read_only=True) as connection:
        generation = await life.jobs.current_generation_on(connection)
        plans = await (await connection.execute(
            "SELECT p.*,coalesce(c.context,p.wake_context,g.context) AS recovery_context FROM life.plan_watermarks p "
            "LEFT JOIN life.routines c USING(actor_id) LEFT JOIN life.plan_generations g ON g.generation_key=p.generation_key "
            "WHERE (c.actor_id IS NULL OR c.enabled) AND (p.planned_watermark<>p.change_watermark OR (p.generation_key IS NOT NULL AND NOT p.generation_consumed) "
            "OR p.repair_due_at IS NOT NULL OR p.review_after IS NOT NULL "
            "OR (p.decision_fingerprint IS NOT NULL AND p.last_error IS NULL AND p.next_due_at IS NOT NULL))")).fetchall()
        work = []
        for table in ("activities", "commitments", "contact_candidates"):
            states = {"activities": ("planned", "running", "dormant"),
                "commitments": ("scheduled", "waiting", "dispatching", "waiting_result", "unknown"),
                "contact_candidates": ("pending", "waiting", "sending", "waiting_expression", "unknown")}[table]
            rows = await (await connection.execute(
                f"SELECT * FROM life.{table} WHERE state=ANY(%s) AND context IS NOT NULL ORDER BY actor_id,id", (list(states),))).fetchall()
            work.extend((table, row) for row in rows)
    for row in plans:
        try:
            if row["recovery_context"] is None:
                continue
            context = await scheduled_context(life, row["recovery_context"])
            await life.identity.check(context)
            pending = row["planned_watermark"] != row["change_watermark"] or (row["generation_key"] and not row["generation_consumed"])
            from .planning_engine import PlanningEngine, decision_review_at
            dates = [value for value in (row["repair_due_at"], decision_review_at(row)) if value is not None]
            async with life.database.transaction() as connection:
                await PlanningEngine(worker, data_generation=generation).schedule(connection,context,row,datetime.now(UTC) if pending else min(dates),"recover")
        except AppError as error:
            if error.code == "job_generation_expired" or error.status not in {401, 403, 404, 409, 410}:
                raise
    for table, row in work:
        try:
            context = await scheduled_context(life, row["context"])
            await life.identity.check(context, source_refs(row["source_refs"]))
            if table == "activities":
                if row["waiting_reason"] in {"video_checkpoint_unavailable", "read_checkpoint_unavailable"}:
                    continue
                if not action_resolved(row):
                    task, payload, due = "life.activity_reconcile", {"activity_id": row["id"], "context": wire(context), "sequence": 0}, None
                elif row["state"] in {"planned", "running"}:
                    task, payload, due = "life.activity_step", {"activity_id": row["id"], "context": wire(context), "expected_revision": row["revision"]}, row["next_due_at"]
                elif (row["state"] == "dormant" and row["waiting_reason"] == "continuation_available"
                        and row["resume_condition"] == "continue_from_checkpoint"
                        and (row["checkpoint_data"].get("last_execution") == "succeeded" or resolved_read_failure(row))):
                    # The handler rechecks the original run's time and step fences.
                    # A pre-upgrade checkpoint without a window signals the planner,
                    # rather than restarting discovery or inventing another slot.
                    task, payload, due = "life.activity_step", {"activity_id": row["id"], "context": wire(context), "expected_revision": row["revision"]}, row["next_due_at"]
                else:
                    continue
            elif table == "commitments":
                if row["action_id"] or row["last_expression_id"]:
                    task, payload, due = "life.commitment_reconcile", {"commitment_id": row["id"], "expected_revision": row["revision"]}, None
                elif row["due_at"]:
                    task, payload, due = "life.commitment_due", {"commitment_id": row["id"], "expected_revision": row["revision"]}, row["due_at"]
                else:
                    continue
            else:
                task = "life.contact_due" if row["state"] in {"pending", "waiting"} else "life.contact_reconcile"
                payload, due = {"candidate_id": row["id"], "expected_revision": row["revision"]}, row["due_at"] if task.endswith("due") else None
            async with life.database.transaction() as connection:
                await life.jobs.enqueue(connection, JobSpec(task, f"life-recover:{table}:{row['id']}:{row['revision']}:{uuid4()}",
                    payload, scheduled_at=max(due, datetime.now(UTC)) if due else None, subject_id=context.actor_id,
                    data_generation=generation))
        except AppError as error:
            if error.code == "job_generation_expired" or error.status not in {401, 403, 404, 409, 410}:
                raise
            # A visible reason remains on the affected object; other actors and
            # independent commitments continue to recover.
            async with life.database.transaction() as connection:
                await life.jobs.assert_generation_on(connection, generation)
                field = "waiting_reason" if table == "activities" else "reason"
                await connection.execute(f"UPDATE life.{table} SET {field}=%s WHERE id=%s AND revision=%s",
                    ("scheduled_authorization_unavailable", row["id"], row["revision"]))
