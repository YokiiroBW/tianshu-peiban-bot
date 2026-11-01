"""One real Procrastinate queue with business changes and enqueue in one transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import inspect
import json
from typing import Any, Mapping

import procrastinate

from tianshu.types import ServiceClass
from .errors import AppError
from .execution import ExecutionLane, TASK_LANES, job_observation


@dataclass(frozen=True, slots=True)
class JobSpec:
    task: str
    key: str
    payload: Mapping[str, Any]
    service_class: ServiceClass = ServiceClass.ELASTIC
    scheduled_at: datetime | None = None
    subject_id: str | None = None
    matter_id: str | None = None
    execution_lane: ExecutionLane | None = None


class JobQueue:
    def __init__(self, dsn: str, *, max_connections=4):
        self.app = procrastinate.App(connector=procrastinate.PsycopgConnector(
            conninfo=dsn, min_size=0, max_size=max_connections))
        self.tasks = {}
        # Two native connection adapters, one and the same Procrastinate schema and task set.
        # The sync adapter only defers on the caller's externally managed transaction.
        self.sync_app = procrastinate.App(connector=procrastinate.SyncPsycopgConnector(
            conninfo=dsn, min_size=0, max_size=1))
        self.sync_tasks = {}
        self.task_lanes = {}
        self.lane_resolvers = {}
        self.events = None

    async def open(self):
        await self.app.open_async()

    async def close(self):
        await self.app.close_async()

    def register(self, name, handler, *, retryable=False, execution_lane=None,
                 lane_resolver=None):
        """Only explicitly reentrant computation handlers may retry automatically."""
        if name in self.tasks:
            raise ValueError("duplicate_task_registration")
        if not inspect.iscoroutinefunction(handler):
            raise TypeError("async_job_handler_required")
        default_lane = TASK_LANES.get(name)
        if execution_lane is None and default_lane is None and lane_resolver is None:
            raise ValueError("task_execution_lane_required")
        lane = self._lane(execution_lane if execution_lane is not None
                          else default_lane or ExecutionLane.MAINTENANCE)
        if lane_resolver is not None:
            if not inspect.iscoroutinefunction(lane_resolver):
                raise TypeError("async_job_lane_resolver_required")
            self.lane_resolvers[name] = lane_resolver
        self.task_lanes[name] = lane

        async def execute(job_context, **kwargs):
            if self.events:
                await self.events.emit("job.started", "started", task_name=name, job_id=job_context.job.id)
            try:
                result = await handler(**kwargs)
            except BaseException as exc:
                if self.events:
                    try:
                        await self.events.emit(
                            "job.failed", "failed", task_name=name, job_id=job_context.job.id,
                            error_code=exc.code if isinstance(exc, AppError) else type(exc).__name__,
                        )
                    except AppError:
                        pass
                raise
            if self.events:
                try:
                    await self.events.emit("job.finished", "succeeded", task_name=name, job_id=job_context.job.id)
                except AppError:
                    pass
            return result

        self.tasks[name] = self.app.task(name=name, retry=3 if retryable else False,
                                         pass_context=True)(execute)
        self.sync_tasks[name] = self.sync_app.task(name=name, retry=3 if retryable else False,
                                                   pass_context=True)(execute)
        return self.tasks[name]

    @staticmethod
    def _lane(value):
        try:
            return ExecutionLane(value)
        except (TypeError, ValueError):
            raise AppError("invalid_execution_lane") from None

    async def _resolve_lane(self, connection, task, payload, explicit=None):
        if explicit is not None:
            return self._lane(explicit)
        resolver = self.lane_resolvers.get(task)
        if resolver:
            return self._lane(await resolver(connection, payload))
        return self.task_lanes[task]

    def enqueue_sync(self, connection, spec: JobSpec) -> int:
        if spec.task not in self.sync_tasks:
            raise AppError("task_not_registered", status=503)
        if spec.service_class not in {ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC}:
            raise AppError("invalid_persistent_job_class")
        inserted = connection.execute(
            "INSERT INTO runtime.job_keys(key,task,subject_id,matter_id) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING key",
            (spec.key, spec.task, spec.subject_id, spec.matter_id)).fetchone()
        if not inserted:
            row = connection.execute("SELECT job_id,task,cancelled_at FROM runtime.job_keys "
                                     "WHERE key=%s FOR UPDATE", (spec.key,)).fetchone()
            if row["task"] != spec.task:
                raise AppError("job_identity_conflict", status=409)
            if row["cancelled_at"]:
                raise AppError("job_cancelled", status=409)
            return row["job_id"]
        if spec.execution_lane is None and spec.task in self.lane_resolvers:
            raise AppError("dynamic_job_lane_required")
        lane = self._lane(spec.execution_lane if spec.execution_lane is not None
                          else self.task_lanes[spec.task])
        job_id = self.sync_tasks[spec.task].configure(connection=connection,
            queue=lane.value, schedule_at=spec.scheduled_at,
            priority=100 if spec.service_class == ServiceClass.DUE else 0).defer(**dict(spec.payload))
        connection.execute("UPDATE runtime.job_keys SET job_id=%s WHERE key=%s", (job_id, spec.key))
        return job_id

    async def enqueue(self, connection, spec: JobSpec) -> int:
        if spec.task not in self.tasks:
            raise AppError("task_not_registered", status=503)
        if spec.service_class not in {ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC}:
            raise AppError("invalid_persistent_job_class")
        cursor = await connection.execute(
            "INSERT INTO runtime.job_keys(key,task,subject_id,matter_id) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING key",
            (spec.key, spec.task, spec.subject_id, spec.matter_id))
        inserted = await cursor.fetchone()
        if not inserted:
            cursor = await connection.execute(
                "SELECT job_id,task,cancelled_at FROM runtime.job_keys WHERE key=%s FOR UPDATE",
                (spec.key,))
            row = await cursor.fetchone()
            if row["task"] != spec.task:
                raise AppError("job_identity_conflict", status=409)
            if row["cancelled_at"]:
                raise AppError("job_cancelled", status=409)
            return row["job_id"]
        lane = await self._resolve_lane(connection, spec.task, spec.payload, spec.execution_lane)
        job_id = await self.tasks[spec.task].configure(
            connection=connection, queue=lane.value,
            schedule_at=spec.scheduled_at,
            # Future work never shares a global actor lock that blocks earlier deadlines.
            priority=100 if spec.service_class == ServiceClass.DUE else 0,
        ).defer_async(**dict(spec.payload))
        await connection.execute("UPDATE runtime.job_keys SET job_id=%s WHERE key=%s",
                                 (job_id, spec.key))
        return job_id

    async def reroute_pending(self, connection, job_id, *, execution_lane,
                              task=None, service_class=None):
        """Correct an unstarted job in its business transaction, retaining identity.

        Action owners must additionally lock and verify their own pending attempt.
        This API never retries a dispatched/doing job or resets an attempt counter.
        """
        lane = self._lane(execution_lane)
        if service_class is not None and service_class not in {
            ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC,
        }:
            raise AppError("invalid_persistent_job_class")
        row = await (await connection.execute(
            "UPDATE procrastinate_jobs SET queue_name=%s,"
            "priority=CASE WHEN %s THEN %s ELSE priority END "
            "WHERE id=%s AND status='todo' AND attempts=0 "
            "AND (%s::text IS NULL OR task_name=%s) RETURNING id",
            (lane.value, service_class is not None,
             100 if service_class == ServiceClass.DUE else 0,
             job_id, task, task))).fetchone()
        if row is not None:
            # Updating queue_name does not fire Procrastinate's INSERT notifier.
            # Use its native notification protocol in this same transaction.
            payload = json.dumps({"type": "job_inserted", "job_id": job_id})
            await connection.execute(
                "SELECT pg_notify(%s,%s),pg_notify('procrastinate_any_queue_v1',%s)",
                ("procrastinate_queue_v1#" + lane.value, payload, payload))
        return row is not None

    async def observe_on(self, connection, job_id=None, *, key=None):
        """Native queue metadata only; a queue row is not a model/effect receipt."""
        if (job_id is None) == (key is None):
            raise ValueError("one_job_identity_required")
        row = await (await connection.execute(
            "SELECT j.id AS job_id,j.status AS job_state,j.queue_name,j.attempts,"
            "j.priority,j.scheduled_at,coalesce(j.scheduled_at>now(),false) AS scheduled "
            "FROM procrastinate_jobs j WHERE "
            "(%s::bigint IS NOT NULL AND j.id=%s) OR "
            "(%s::text IS NOT NULL AND j.id=(SELECT job_id FROM runtime.job_keys WHERE key=%s))",
            (job_id, job_id, key, key))).fetchone()
        if row is None:
            return None
        return job_observation(row)

    async def route_legacy_pending(self, database, *, batch_size=128):
        """One bounded-pass metadata conversion before starting lane consumers.

        The old queue names are never consumed by the new process. Previously
        retried todo jobs retain their attempts, scheduling, locks and payloads;
        doing/terminal jobs and the model/action ledgers are never changed.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 512:
            raise ValueError("invalid_job_routing_batch_size")
        legacy = ["interactive", "due", "elastic"]
        async with database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT max(id) AS cutoff FROM procrastinate_jobs "
                "WHERE status='todo' AND queue_name=ANY(%s)", (legacy,))).fetchone()
        cutoff = row["cutoff"]
        after = 0
        routed = 0
        blocked_count = 0
        blocked = []
        while cutoff is not None and after < cutoff:
            async with database.transaction(read_only=True) as connection:
                rows = await (await connection.execute(
                    "SELECT id,task_name FROM procrastinate_jobs "
                    "WHERE status='todo' AND queue_name=ANY(%s) AND id>%s AND id<=%s "
                    "ORDER BY id LIMIT %s",
                    (legacy, after, cutoff, batch_size))).fetchall()
            if not rows:
                break
            for candidate in rows:
                try:
                    # A resolver's SQL failure must roll back only this original
                    # item, not poison the page or prevent unrelated workers.
                    async with database.transaction() as connection:
                        row = await (await connection.execute(
                            "SELECT id,task_name,args FROM procrastinate_jobs WHERE id=%s "
                            "AND status='todo' AND queue_name=ANY(%s) FOR UPDATE",
                            (candidate["id"], legacy))).fetchone()
                        if row is None:
                            continue
                        if row["task_name"] not in self.tasks:
                            raise AppError("legacy_job_task_not_registered", status=503)
                        lane = await self._resolve_lane(connection, row["task_name"], row["args"])
                        await connection.execute(
                            "UPDATE procrastinate_jobs SET queue_name=%s WHERE id=%s",
                            (lane.value, row["id"]))
                    routed += 1
                except Exception as error:
                    code = error.code if isinstance(error, AppError) else "job_lane_resolution_failed"
                    if (not isinstance(code, str) or not 1 <= len(code) <= 96 or not all(
                        character.isalnum() or character in "_.-" for character in code
                    )):
                        code = "job_lane_resolution_failed"
                    blocked_count += 1
                    if len(blocked) < 128:
                        blocked.append({"job_id": candidate["id"],
                                        "task_name": candidate["task_name"], "error_code": code})
                    if self.events:
                        try:
                            await self.events.emit("job.routing_blocked", "rejected",
                                task_name=candidate["task_name"], job_id=candidate["id"], error_code=code)
                        except AppError:
                            pass
            after = rows[-1]["id"]
        return {"routed": routed, "cutoff": cutoff, "blocked_count": blocked_count,
                "blocked": blocked, "blocked_complete": blocked_count == len(blocked)}

    async def cancel(self, connection, key: str):
        cursor = await connection.execute(
            "SELECT job_id FROM runtime.job_keys WHERE key=%s FOR UPDATE", (key,))
        row = await cursor.fetchone()
        if row:
            await self.app.job_manager.cancel_job_by_id_async(
                row["job_id"], abort=True, connection=connection)
            await connection.execute("UPDATE runtime.job_keys SET cancelled_at=now() WHERE key=%s",
                                     (key,))
        return bool(row)
