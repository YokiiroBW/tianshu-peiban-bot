"""Persisted local-day rhythm; only new clock boundaries make planning signals."""

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from tianshu.conversation.codec import context_from_record, document, signature
from .validation import revision, timezone
from .planning import signal


PLANNING_STATUS_CODES = frozenset({
    "planned", "planned_with_reduced_context", "activity_planned",
    "no_meaningful_activity", "waiting_for_next_life_window",
    "existing_activity_continues", "current_activity_continues",
    "planner_output_invalid", "planner_output_incomplete", "planner_context_budget_exceeded",
    "planner_capability_unavailable", "planner_arguments_invalid", "planner_goal_unavailable",
    "planner_original_contract_missing", "planner_original_context_changed",
    "planner_original_request_missing", "original_planner_not_registered_before_deadline",
    "planner_unknown", "planner_queued", "planner_running", "planner_failed", "planner_cancelled",
})


def hour(value):
    try:
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise AppError("invalid_routine_time") from None


def awake_and_due(row, now):
    local = now.astimezone(ZoneInfo(row["timezone"]))
    wake, sleep = hour(row["wake_at"]), hour(row["sleep_at"])
    current = local.time().replace(tzinfo=None)
    awake = (wake <= current < sleep) if wake < sleep else (current >= wake or current < sleep)
    if awake:
        due = local + timedelta(seconds=row["idle_seconds"])
        next_sleep = datetime.combine(local.date(), sleep, local.tzinfo)
        if next_sleep <= local:
            next_sleep += timedelta(days=1)
        due = min(due, next_sleep)
    else:
        due = datetime.combine(local.date(), wake, local.tzinfo)
        if due <= local:
            due += timedelta(days=1)
    return awake, due.astimezone(UTC)


def authorization_blocked(error):
    return isinstance(error, AppError) and (
        error.status in {401, 403} or error.code in {
            "actor_disabled", "access_changed", "delivery_access_changed",
        }
    )


class LifeClock:
    def __init__(self, database, commands, jobs, identity, sources):
        self.database, self.commands, self.jobs = database, commands, jobs
        self.identity, self.sources = identity, sources

    async def activate_later(self, context, actor_id, *, revision, connection):
        await self.jobs.enqueue(connection, JobSpec(
            "life.activate", f"life-activate:{actor_id}:{revision}",
            {"actor_id": actor_id, "context": wire(context)},
            ServiceClass.INTERACTIVE, subject_id=actor_id,
        ))

    async def activate(self, actor_id, context):
        previous = context_from_record(context)
        if previous.actor_id != actor_id:
            raise AppError("actor_scope_mismatch", status=403)
        await self.ensure(previous)

    async def _authorize(self, previous, connection):
        current = await self.identity.refresh_scheduled_context(
            previous, permission="life.manage", connection=connection,
        )
        if not await self.sources.validate_persistence(connection, current, ()):
            raise AppError("scheduled_authorization_unavailable", status=403)
        return current

    async def ensure(self, context):
        """Role activation retains an existing author's fixed scene and settings."""
        try:
            async with self.database.transaction() as connection:
                old = await (await connection.execute(
                    "SELECT * FROM life.routines WHERE actor_id=%s", (context.actor_id,),
                )).fetchone()
                previous = context_from_record(old["context"]) if old else context
                current = await self._authorize(previous, connection)
                if current.actor_id != context.actor_id:
                    raise AppError("actor_scope_mismatch", status=403)
                row = await (await connection.execute(
                    "INSERT INTO life.routines(actor_id,context,next_due_at) VALUES (%s,%s,now()) "
                    "ON CONFLICT(actor_id) DO UPDATE SET context=excluded.context "
                    "WHERE life.routines.revision=%s AND life.routines.context=%s RETURNING *",
                    (current.actor_id, document(current), old["revision"] if old else 0,
                     document(old["context"]) if old else None),
                )).fetchone()
                if row and row["enabled"]:
                    now = (await (await connection.execute("SELECT now() AS value")).fetchone())["value"]
                    await self._enqueue(connection, row, recovery=old is not None and row["next_due_at"] <= now)
        except AppError as error:
            if not authorization_blocked(error):
                raise

    async def view(self, context):
        await self.identity.check(context)
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM life.routines WHERE actor_id=%s", (context.actor_id,),
            )).fetchone()
            planning = await (await connection.execute(
                "SELECT revision,reason,next_due_at,last_planned_at,generation_consumed,"
                "planned_watermark<>change_watermark AS pending "
                "FROM life.plan_watermarks WHERE actor_id=%s", (context.actor_id,),
            )).fetchone()
            now = (await (await connection.execute("SELECT now() AS value")).fetchone())["value"]
            status, reason = "not_started", None
            if row:
                status = "disabled" if not row["enabled"] else "waiting"
                if row["enabled"]:
                    try:
                        await self.identity.refresh_scheduled_context(
                            context_from_record(row["context"]),
                            permission="life.manage", connection=connection,
                        )
                    except AppError as error:
                        if not authorization_blocked(error):
                            raise
                        status, reason = "blocked", error.code
                    else:
                        if row["next_due_at"] <= now:
                            status = "overdue"
        value = ({key: row[key] for key in (
            "actor_id", "revision", "enabled", "timezone", "wake_at", "sleep_at",
            "idle_seconds", "next_due_at", "last_tick_at",
        )} if row else {
            "actor_id": context.actor_id, "revision": None, "enabled": True,
            "timezone": "Asia/Shanghai", "wake_at": "08:00", "sleep_at": "23:00",
            "idle_seconds": 5400, "next_due_at": None, "last_tick_at": None,
        })
        if planning and planning["reason"]:
            # Planner prose can depend on a different private conversation. Only
            # technical status codes belong in this actor-level health view.
            code = planning["reason"]
            if code not in PLANNING_STATUS_CODES:
                planning = {**planning, "reason": "activity_planned"}
        return {**value, "schedule_status": status, "schedule_reason": reason,
                "planning": planning, "observed_at": now}

    async def save(self, context, body):
        await self.identity.check(context)
        wake, sleep = body.get("wake_at"), body.get("sleep_at")
        if hour(wake) == hour(sleep):
            raise AppError("invalid_routine_window")
        zone = timezone(body.get("timezone"))
        idle = body.get("idle_seconds")
        if type(idle) is not int or not 900 <= idle <= 86400 or type(body.get("enabled")) is not bool:
            raise AppError("invalid_routine")

        async def apply(connection):
            current_context = await self._authorize(context, connection)
            old = await (await connection.execute(
                "SELECT * FROM life.routines WHERE actor_id=%s FOR UPDATE", (context.actor_id,),
            )).fetchone()
            revision(old, body.get("expected_revision"))
            current = old["revision"] + 1 if old else 1
            row = await (await connection.execute(
                "INSERT INTO life.routines(actor_id,revision,enabled,timezone,wake_at,sleep_at,"
                "idle_seconds,context,next_due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()) "
                "ON CONFLICT(actor_id) DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,"
                "timezone=excluded.timezone,wake_at=excluded.wake_at,sleep_at=excluded.sleep_at,"
                "idle_seconds=excluded.idle_seconds,context=excluded.context,next_due_at=excluded.next_due_at RETURNING *",
                (context.actor_id, current, body["enabled"], zone, wake, sleep, idle, document(current_context)),
            )).fetchone()
            if row["enabled"]:
                await self._enqueue(connection, row)
            await connection.execute(
                "UPDATE life.actor_state SET timezone=%s,revision=revision+1 WHERE actor_id=%s",
                (zone, context.actor_id),
            )
            return CommandResult(body["client_request_id"], {"actor_id": context.actor_id, "revision": current}, current)
        return await self.commands.execute(
            context, "life.routines.save", body["client_request_id"], signature(body), apply,
        )

    async def _enqueue(self, connection, row, *, recovery=False):
        key = f"life-clock:{row['actor_id']}:{row['revision']}:{row['next_due_at'].isoformat()}"
        if recovery:
            # A failed Procrastinate job keeps its original dedupe key forever.
            # Only this reentrant timer gets a new control identity. Its revision
            # and due-at CAS fence duplicates; no business attempt is reset.
            key += ":recover:" + str(uuid4())
        await self.jobs.enqueue(connection, JobSpec(
            "life.clock", key,
            {"actor_id": row["actor_id"], "expected_revision": row["revision"],
             "due_at": row["next_due_at"].isoformat()},
            ServiceClass.DUE, row["next_due_at"], row["actor_id"],
        ))

    async def tick(self, actor_id, expected_revision, due_at):
        try:
            async with self.database.transaction() as connection:
                row = await (await connection.execute(
                    "SELECT * FROM life.routines WHERE actor_id=%s", (actor_id,),
                )).fetchone()
                if not row or not row["enabled"] or row["revision"] != expected_revision or row["next_due_at"].isoformat() != due_at:
                    return
                previous = context_from_record(row["context"])
                if previous.actor_id != actor_id:
                    raise AppError("actor_scope_mismatch", status=403)
                context = await self._authorize(previous, connection)
                fresh = await (await connection.execute(
                    "SELECT * FROM life.routines WHERE actor_id=%s FOR UPDATE", (actor_id,),
                )).fetchone()
                now = (await (await connection.execute("SELECT now() AS value")).fetchone())["value"]
                if not fresh["enabled"] or fresh["revision"] != expected_revision or fresh["next_due_at"].isoformat() != due_at or fresh["next_due_at"] > now:
                    return
                awake, due = awake_and_due(fresh, now)
                await connection.execute(
                    "UPDATE life.routines SET context=%s,next_due_at=%s,last_tick_at=%s WHERE actor_id=%s",
                    (document(context), due, now, actor_id),
                )
                await self._enqueue(connection, {**fresh, "next_due_at": due})
                if awake:
                    await signal(connection, self.jobs, context, "rhythm:" + due_at)
        except AppError as error:
            if not authorization_blocked(error):
                raise

    async def recover(self):
        """Refresh legitimate configurations before LifeWorker reads their context."""
        async with self.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT * FROM life.routines WHERE enabled=true ORDER BY actor_id",
            )).fetchall()
        resumed, blocked = 0, 0
        for row in rows:
            try:
                async with self.database.transaction() as connection:
                    previous = context_from_record(row["context"])
                    if previous.actor_id != row["actor_id"]:
                        raise AppError("actor_scope_mismatch", status=403)
                    context = await self._authorize(previous, connection)
                    fresh = await (await connection.execute(
                        "SELECT * FROM life.routines WHERE actor_id=%s FOR UPDATE", (row["actor_id"],),
                    )).fetchone()
                    if not fresh["enabled"] or fresh["revision"] != row["revision"] or fresh["next_due_at"] != row["next_due_at"]:
                        continue
                    await connection.execute(
                        "UPDATE life.routines SET context=%s WHERE actor_id=%s",
                        (document(context), row["actor_id"]),
                    )
                    now = (await (await connection.execute("SELECT now() AS value")).fetchone())["value"]
                    await self._enqueue(connection, fresh, recovery=fresh["next_due_at"] <= now)
                    resumed += 1
            except AppError as error:
                if not authorization_blocked(error):
                    raise
                blocked += 1
        return {"resumed": resumed, "blocked": blocked}
