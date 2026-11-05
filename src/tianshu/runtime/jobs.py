"""One real Procrastinate queue with business changes and enqueue in one transaction."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import inspect
import json
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

import procrastinate
from procrastinate.exceptions import AppNotOpen
from procrastinate.manager import JobManager
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision, RetryStrategy
from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb

from tianshu.types import ServiceClass
from .database import classify_database_failure, pool_application_name
from .errors import AppError
from .execution import ExecutionLane, TASK_LANES, job_observation, role_for_lane
from .recovery_policy import RecoveryWait, recovery_wait_retryable

DYNAMIC_TASK_LANES = frozenset({
    "conversation.run", "conversation.prepare", "capabilities.execute", "capabilities.media_job",
})
_execution_generation: ContextVar[str | None] = ContextVar("tianshu_execution_generation", default=None)


def current_execution_generation() -> str | None:
    """The current native job's original envelope generation, never a DB lookup."""
    return _execution_generation.get()


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
    payload_version: int = 1
    data_generation: str | None = None


@dataclass(frozen=True, slots=True)
class RecomputeClaim:
    domain: str
    key: str
    revision: int
    token: str
    payload: Mapping[str, Any]
    data_generation: str


@dataclass(frozen=True, slots=True)
class RecomputeCoverage:
    """Material gaps are explicit; repairing one never removes its history."""
    isolated: Mapping[str, str] = field(default_factory=dict)
    repaired: tuple[str, ...] = ()


class FairJobManager(JobManager):
    """Rotate queues and claim by subject using the native job ledger.

    The Procrastinate worker has one fetch loop per group. This prevents a
    continuously busy higher-priority queue from starving the other queues in
    that group without creating a second queue or long-lived database lock.
    """

    def __init__(self, connector):
        super().__init__(connector=connector)
        self._next_queue = {}

    async def fetch_job(self, queues, worker_id):
        values = tuple(queues or ())
        if queues is None:
            return await super().fetch_job(queues=None, worker_id=worker_id)
        if not values:
            return None
        start = self._next_queue.get(values, 0)
        for offset in range(len(values)):
            index = (start + offset) % len(values)
            job = await self._fetch_one(values[index], worker_id)
            if job is not None:
                self._next_queue[values] = (index + 1) % len(values)
                return job
        return None

    async def _fetch_one(self, queue, worker_id):
        row = await self.connector.execute_query_one_async(
            query="SELECT * FROM runtime.fair_claim_job(%(queue)s,%(worker_id)s)",
            queue=queue, worker_id=worker_id)
        return Job.from_row(row) if row["id"] is not None else None


class ResourceRecoveryRetry(BaseRetryStrategy):
    """Reawaken only an explicitly registered durable-state recovery handler."""

    def __init__(self, *, retryable=False, resource_recovery=True):
        self.ordinary = RetryStrategy(max_attempts=3) if retryable else None
        self.resource_recovery = resource_recovery

    def get_retry_decision(self, *, exception, job):
        if isinstance(exception, RecoveryWait):
            return (RetryDecision(retry_at=exception.retry_at)
                    if self.resource_recovery and recovery_wait_retryable(exception) else None)
        if not self.resource_recovery:
            return self.ordinary.get_retry_decision(exception=exception, job=job) if self.ordinary else None
        failure = classify_database_failure(exception)
        if failure:
            if not failure.retryable:
                return None
            # A resource wait does not consume a Models attempt. Keep the same
            # native job identity while allowing other jobs to run.
            delay = min(300, 10 * 2 ** min(job.attempts, 5))
            return RetryDecision(retry_in={"seconds": delay})
        if isinstance(exception, PsycopgError):
            # Without phase/rollback evidence even an ordinary retry would risk
            # replaying a transaction whose commit outcome is unknown.
            return None
        return self.ordinary.get_retry_decision(exception=exception, job=job) if self.ordinary else None


def _identity_digest(spec: JobSpec) -> str:
    if type(spec.payload_version) is not int or spec.payload_version < 1:
        raise AppError("invalid_job_payload_version")
    try:
        document = json.dumps({
            "task": spec.task, "payload": dict(spec.payload),
            "subject_id": spec.subject_id, "matter_id": spec.matter_id,
            "payload_version": spec.payload_version, "data_generation": spec.data_generation,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise AppError("invalid_job_payload") from None
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


class JobQueue:
    def __init__(self, dsn: str, *, max_connections=4, database=None):
        self.app = procrastinate.App(connector=procrastinate.PsycopgConnector(
            conninfo=dsn, min_size=0, max_size=max_connections,
            kwargs={"application_name": pool_application_name(getattr(database, "role", None), "queue")}))
        self.app.job_manager = FairJobManager(self.app.connector)
        self.tasks = {}
        # Two native connection adapters, one and the same Procrastinate schema and task set.
        # The sync adapter only defers on the caller's externally managed transaction.
        self.sync_app = procrastinate.App(connector=procrastinate.SyncPsycopgConnector(
            conninfo=dsn, min_size=0, max_size=1))
        # Producer-only task definitions use the caller's transaction. They are
        # kept out of the worker app's executable registry and never open pools.
        self.route_app = procrastinate.App(connector=procrastinate.PsycopgConnector(
            conninfo=dsn, min_size=0, max_size=1))
        self.sync_route_app = procrastinate.App(connector=procrastinate.SyncPsycopgConnector(
            conninfo=dsn, min_size=0, max_size=1))
        self.sync_tasks = {}
        self.route_tasks = {}
        self.sync_route_tasks = {}
        self.recompute_routes = set()
        self.recompute_tasks = set()
        self.task_lanes = {}
        self.lane_resolvers = {}
        self.unresolved_lanes = set()
        self.events = None
        self.database = database

    async def open(self):
        await self.app.open_async()

    async def close(self):
        await self.app.close_async()

    def pool_stats(self):
        try:
            return self.app.connector.pool.get_stats()
        except AppNotOpen:
            return None

    @staticmethod
    async def current_generation_on(connection):
        row = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true")).fetchone()
        if not row:
            raise AppError("installation_generation_missing", status=503)
        return row["id"]

    @staticmethod
    async def assert_generation_on(connection, generation):
        """Fence a domain write in its own short commit transaction."""
        row = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        if not row or row["id"] != generation:
            raise AppError("job_generation_expired", status=409)
        return row["id"]

    async def current_generation(self):
        if self.database is None:
            raise ValueError("generation_database_required")
        async with self.database.transaction(read_only=True) as connection:
            return await self.current_generation_on(connection)

    @staticmethod
    def current_generation_sync_on(connection):
        row = connection.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true").fetchone()
        if not row:
            raise AppError("installation_generation_missing", status=503)
        return row["id"]

    @staticmethod
    def assert_generation_sync_on(connection, generation):
        row = connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE").fetchone()
        if not row or row["id"] != generation:
            raise AppError("job_generation_expired", status=409)
        return row["id"]

    async def _bind_generation_on(self, connection, spec):
        row = await (await connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
        if not row:
            raise AppError("installation_generation_missing", status=503)
        if spec.data_generation is not None and spec.data_generation != row["id"]:
            raise AppError("job_generation_expired", status=409)
        return replace(spec, data_generation=row["id"])

    @staticmethod
    def _bind_generation_sync(connection, spec):
        row = connection.execute(
            "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE").fetchone()
        if not row:
            raise AppError("installation_generation_missing", status=503)
        if spec.data_generation is not None and spec.data_generation != row["id"]:
            raise AppError("job_generation_expired", status=409)
        return replace(spec, data_generation=row["id"])

    def register_route(self, name, *, execution_lane=None, lane_resolver=None):
        """Register producer metadata without constructing or starting its consumer."""
        if name in self.route_tasks:
            raise ValueError("duplicate_route_registration")
        lane = execution_lane if execution_lane is not None else TASK_LANES.get(name)
        if lane is None and lane_resolver is None:
            raise ValueError("task_execution_lane_required")
        if lane_resolver is not None and not inspect.iscoroutinefunction(lane_resolver):
            raise TypeError("async_job_lane_resolver_required")
        if name not in self.tasks:
            self.task_lanes[name] = self._lane(lane or ExecutionLane.MAINTENANCE)
            if lane_resolver is not None:
                self.lane_resolvers[name] = lane_resolver
                self.unresolved_lanes.discard(name)
            elif name in DYNAMIC_TASK_LANES and execution_lane is None:
                self.unresolved_lanes.add(name)

        async def producer_only(**_kwargs):
            raise RuntimeError("producer_route_not_executable")

        self.route_tasks[name] = self.route_app.task(name=name)(producer_only)
        self.sync_route_tasks[name] = self.sync_route_app.task(name=name)(producer_only)

    def register_static_routes(self):
        for name in TASK_LANES:
            if name not in self.route_tasks:
                self.register_route(name)

    def register_recompute_route(self, name, *, execution_lane=None):
        """Opt a producer route into coalescing; ordinary jobs cannot coalesce here."""
        if name not in self.route_tasks:
            self.register_route(name, execution_lane=execution_lane)
        elif execution_lane is not None and self.task_lanes[name] != self._lane(execution_lane):
            raise ValueError("recompute_route_lane_conflict")
        self.recompute_routes.add(name)

    def register(self, name, handler, *, retryable=False, execution_lane=None,
                 lane_resolver=None, resource_recovery=False):
        """Register a handler; resource recovery requires a durable-state owner.

        Opted-in handlers must resume from the original persisted state and
        receipt, not blindly repeat an external effect. Only confirmed local
        resource failures are rescheduled; ordinary retry remains independent.
        """
        if type(resource_recovery) is not bool:
            raise ValueError("invalid_resource_recovery_registration")
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
            self.unresolved_lanes.discard(name)
        elif execution_lane is not None:
            self.unresolved_lanes.discard(name)
        self.task_lanes[name] = lane

        async def execute(job_context, **kwargs):
            generation_token = None
            try:
                row = await self.app.connector.execute_query_one_async(
                    query="SELECT (SELECT k.data_generation FROM runtime.job_keys k "
                    "JOIN runtime.installation_generation g ON g.singleton=true AND k.data_generation=g.id "
                    "WHERE k.job_id=%(job_id)s AND k.cancelled_at IS NULL LIMIT 1) AS data_generation",
                    job_id=job_context.job.id)
                if row["data_generation"] is None:
                    raise AppError("job_generation_expired", status=409)
                generation_token = _execution_generation.set(row["data_generation"])
                if self.events:
                    try:
                        await self.events.emit("job.started", "started", task_name=name, job_id=job_context.job.id)
                    except Exception:
                        pass
                try:
                    result = await handler(**kwargs)
                except BaseException as exc:
                    resource_failure = classify_database_failure(exc)
                    recovery_wait = exc if resource_recovery and recovery_wait_retryable(exc) else None
                    deferred = bool(resource_recovery and
                                    (recovery_wait or (resource_failure and resource_failure.retryable)))
                    if deferred and job_context.job.queueing_lock:
                        try:
                            # A concurrent equivalent wake may already occupy the
                            # todo-only lock. Keep this original job/key/payload
                            # and the other wake; domain CAS coalesces their work.
                            await self.app.connector.execute_query_async(
                                query="UPDATE procrastinate_jobs SET queueing_lock=NULL "
                                "WHERE id=%(job_id)s AND status='doing'", job_id=job_context.job.id)
                        except Exception:
                            pass  # The queue's own retry persistence remains authoritative.
                    if self.events:
                        try:
                            await self.events.emit(
                                "job.deferred" if deferred else "job.failed", "deferred" if deferred else "failed",
                                task_name=name, job_id=job_context.job.id,
                                error_code=(recovery_wait.reason if recovery_wait else resource_failure.reason
                                            if resource_failure else exc.code if isinstance(exc, AppError)
                                            else type(exc).__name__),
                                dependency_ref=(f"database:{resource_failure.pool_name}" if resource_failure
                                                and resource_failure.pool_name else recovery_wait.dependency
                                                if recovery_wait else None),
                                **({"failure_phase": resource_failure.failure_phase, "pool_name": resource_failure.pool_name}
                                   if resource_failure else {}),
                            )
                        except Exception:
                            pass
                    raise
                if self.events:
                    try:
                        await self.events.emit("job.finished", "succeeded", task_name=name, job_id=job_context.job.id)
                    except Exception:
                        pass
                return result
            finally:
                if generation_token is not None:
                    _execution_generation.reset(generation_token)
                try:
                    await asyncio.shield(self.app.connector.execute_query_async(
                        query="SELECT runtime.finish_fair_job(%(job_id)s)", job_id=job_context.job.id))
                except Exception:
                    pass

        retry = (ResourceRecoveryRetry(retryable=retryable, resource_recovery=resource_recovery)
                 if resource_recovery or retryable else False)
        self.tasks[name] = self.app.task(name=name, retry=retry,
                                         pass_context=True)(execute)
        self.sync_tasks[name] = self.sync_app.task(name=name, retry=retry,
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
        if task in self.unresolved_lanes:
            raise AppError("dynamic_job_lane_required")
        resolver = self.lane_resolvers.get(task)
        if resolver:
            return self._lane(await resolver(connection, payload))
        return self.task_lanes[task]

    def enqueue_sync(self, connection, spec: JobSpec) -> int:
        if spec.task not in self.sync_tasks and spec.task not in self.sync_route_tasks:
            raise AppError("task_not_registered", status=503)
        if spec.service_class not in {ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC}:
            raise AppError("invalid_persistent_job_class")
        spec = self._bind_generation_sync(connection, spec)
        digest = _identity_digest(spec)
        if spec.execution_lane is None and (spec.task in self.lane_resolvers or spec.task in self.unresolved_lanes):
            raise AppError("dynamic_job_lane_required")
        lane = self._lane(spec.execution_lane if spec.execution_lane is not None
                          else self.task_lanes[spec.task])
        inserted = connection.execute(
            "INSERT INTO runtime.job_keys(key,task,subject_id,matter_id,payload_digest,payload_version,data_generation) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING key",
            (spec.key, spec.task, spec.subject_id, spec.matter_id, digest, spec.payload_version, spec.data_generation)).fetchone()
        if not inserted:
            row = connection.execute("SELECT job_id,task,cancelled_at,payload_digest,subject_id,matter_id,payload_version,data_generation FROM runtime.job_keys "
                                     "WHERE key=%s FOR UPDATE", (spec.key,)).fetchone()
            if row["task"] != spec.task or (row["payload_digest"] is not None and row["payload_digest"] != digest):
                raise AppError("job_identity_conflict", status=409)
            if row["payload_digest"] is None:
                native = connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (row["job_id"],)).fetchone()
                if (not native or native["args"] != dict(spec.payload)
                        or row["subject_id"] != spec.subject_id or row["matter_id"] != spec.matter_id
                        or row["payload_version"] != spec.payload_version or row["data_generation"] != spec.data_generation):
                    raise AppError("job_identity_conflict", status=409)
                connection.execute("UPDATE runtime.job_keys SET payload_digest=%s WHERE key=%s", (digest, spec.key))
            if row["cancelled_at"]:
                raise AppError("job_cancelled", status=409)
            return row["job_id"]
        task = self.sync_tasks.get(spec.task) or self.sync_route_tasks[spec.task]
        job_id = task.configure(connection=connection,
            queue=lane.value, schedule_at=spec.scheduled_at,
            priority=100 if spec.service_class == ServiceClass.DUE else 0).defer(**dict(spec.payload))
        connection.execute("UPDATE runtime.job_keys SET job_id=%s WHERE key=%s", (job_id, spec.key))
        return job_id

    async def enqueue(self, connection, spec: JobSpec, *, queueing_lock=None) -> int:
        """Optionally merge equivalent wake signals under the caller's business lock.

        Ordinary jobs retain permanent key idempotency. A coalescing caller must
        serialize its resource and use a fresh key for each new physical wake.
        Procrastinate's queueing lock limits only todo, allowing a doing job to
        retain one successor without ever cancelling that running job.
        """
        if spec.task not in self.tasks and spec.task not in self.route_tasks:
            raise AppError("task_not_registered", status=503)
        if spec.service_class not in {ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC}:
            raise AppError("invalid_persistent_job_class")
        spec = await self._bind_generation_on(connection, spec)
        if queueing_lock is not None:
            if not isinstance(queueing_lock, str) or not queueing_lock:
                raise AppError("invalid_job_queueing_lock")
            queueing_lock = queueing_lock + ":generation:" + spec.data_generation
        digest = _identity_digest(spec)
        # Check an existing identity before considering a coalesced signal.
        row = await (await connection.execute(
            "SELECT job_id,task,cancelled_at,payload_digest,subject_id,matter_id,payload_version,data_generation "
            "FROM runtime.job_keys WHERE key=%s", (spec.key,))).fetchone()
        if row:
            if row["task"] != spec.task or (row["payload_digest"] is not None and row["payload_digest"] != digest):
                raise AppError("job_identity_conflict", status=409)
            if row["cancelled_at"]:
                raise AppError("job_cancelled", status=409)
            pending = None
            if queueing_lock is not None:
                pending = await self._coalesce_pending(connection, spec, queueing_lock)
            row = await (await connection.execute(
                "SELECT job_id,task,cancelled_at,payload_digest,subject_id,matter_id,payload_version,data_generation "
                "FROM runtime.job_keys WHERE key=%s FOR UPDATE", (spec.key,))).fetchone()
            await self._check_identity_on(connection, spec, digest, row)
            return pending if pending is not None else row["job_id"]
        if queueing_lock is not None:
            if not isinstance(queueing_lock, str) or not queueing_lock:
                raise AppError("invalid_job_queueing_lock")
            pending = await self._coalesce_pending(connection, spec, queueing_lock)
            if pending is not None:
                return await self._record_coalesced_key(connection, spec, digest, pending)
        lane = await self._resolve_lane(connection, spec.task, spec.payload, spec.execution_lane)
        cursor = await connection.execute(
            "INSERT INTO runtime.job_keys(key,task,subject_id,matter_id,payload_digest,payload_version,data_generation) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING key",
            (spec.key, spec.task, spec.subject_id, spec.matter_id, digest, spec.payload_version, spec.data_generation))
        inserted = await cursor.fetchone()
        if not inserted:
            cursor = await connection.execute(
                "SELECT job_id,task,cancelled_at,payload_digest,subject_id,matter_id,payload_version,data_generation FROM runtime.job_keys WHERE key=%s FOR UPDATE",
                (spec.key,))
            row = await cursor.fetchone()
            await self._check_identity_on(connection, spec, digest, row)
            return row["job_id"]
        task = self.tasks.get(spec.task) or self.route_tasks[spec.task]
        job_id = await task.configure(
            connection=connection, queue=lane.value,
            schedule_at=spec.scheduled_at, queueing_lock=queueing_lock,
            # Future work never shares a global actor lock that blocks earlier deadlines.
            priority=100 if spec.service_class == ServiceClass.DUE else 0,
        ).defer_async(**dict(spec.payload))
        await connection.execute("UPDATE runtime.job_keys SET job_id=%s WHERE key=%s",
                                 (job_id, spec.key))
        return job_id

    @staticmethod
    async def _check_identity_on(connection, spec, digest, row):
        if row["task"] != spec.task or (row["payload_digest"] is not None and row["payload_digest"] != digest):
            raise AppError("job_identity_conflict", status=409)
        if row["payload_digest"] is None:
            native = await (await connection.execute(
                "SELECT args FROM procrastinate_jobs WHERE id=%s", (row["job_id"],))).fetchone()
            if (not native or native["args"] != dict(spec.payload)
                    or row["subject_id"] != spec.subject_id or row["matter_id"] != spec.matter_id
                    or row["payload_version"] != spec.payload_version or row["data_generation"] != spec.data_generation):
                raise AppError("job_identity_conflict", status=409)
            await connection.execute("UPDATE runtime.job_keys SET payload_digest=%s WHERE key=%s", (digest, spec.key))
        if row["cancelled_at"]:
            raise AppError("job_cancelled", status=409)

    async def _record_coalesced_key(self, connection, spec, digest, job_id):
        inserted = await (await connection.execute(
            "INSERT INTO runtime.job_keys(key,task,subject_id,matter_id,payload_digest,payload_version,data_generation,job_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING job_id",
            (spec.key, spec.task, spec.subject_id, spec.matter_id, digest, spec.payload_version,
             spec.data_generation, job_id))).fetchone()
        if inserted:
            return inserted["job_id"]
        row = await (await connection.execute(
            "SELECT job_id,task,cancelled_at,payload_digest,subject_id,matter_id,payload_version,data_generation "
            "FROM runtime.job_keys WHERE key=%s FOR UPDATE", (spec.key,))).fetchone()
        await self._check_identity_on(connection, spec, digest, row)
        return row["job_id"]

    def register_recompute(self, name: str, prepare: Callable[[RecomputeClaim], Awaitable[Callable]],
                           *, execution_lane=None, lease_seconds=60):
        """Register a short, coalescible recalculation, never a command or effect.

        ``prepare`` runs without a database connection and returns an async
        ``apply(connection, claim)`` callback. The callback writes the domain
        result and returns RecomputeCoverage in the same transaction as its
        receipt. It must not perform network I/O. A long operation must instead
        persist an external handle and arrange a later notification.
        """
        if self.database is None:
            raise ValueError("recompute_database_required")
        if not inspect.iscoroutinefunction(prepare) or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_recompute_handler")

        async def execute(domain, key, generation):
            claim = await self._claim_recompute(domain, key, generation, lease_seconds)
            if claim is None:
                return
            try:
                apply = await prepare(claim)
                if not callable(apply):
                    raise ValueError("recompute_apply_required")
                async with self.database.transaction() as connection:
                    await self._complete_recompute_on(connection, claim, apply)
            except BaseException as exc:
                # A failed attempt stays visible and is not implicitly replayed.
                # Cancellation/worker death may interrupt this cleanup; expiry
                # allows the bounded recovery scan to fence the old owner.
                try:
                    async with self.database.transaction() as connection:
                        code = exc.code if isinstance(exc, AppError) else type(exc).__name__
                        await connection.execute(
                            "UPDATE runtime.recompute_attempts SET outcome='failed',error_code=%s,finished_at=now() "
                            "WHERE claim_token=%s AND outcome='running'", (code, claim.token))
                        current = await (await connection.execute(
                            "SELECT * FROM runtime.recompute_work WHERE data_generation=%s AND domain=%s AND work_key=%s FOR UPDATE",
                            (claim.data_generation, domain, key))).fetchone()
                        if current and current["claim_token"] == claim.token:
                            await connection.execute(
                                "UPDATE runtime.recompute_work SET claim_token=NULL,claim_revision=NULL,"
                                "claim_expires_at=NULL,last_error=%s,due_at=NULL "
                                "WHERE data_generation=%s AND domain=%s AND work_key=%s", (code, claim.data_generation, domain, key))
                            if current["dirty_revision"] > claim.revision:
                                await connection.execute(
                                    "UPDATE runtime.recompute_work SET last_error=NULL WHERE data_generation=%s AND domain=%s AND work_key=%s",
                                    (claim.data_generation, domain, key))
                                await self._schedule_recompute_on(connection, current, current["due_at"])
                except Exception:
                    pass
                raise

        self.register(name, execute, execution_lane=execution_lane)
        self.recompute_routes.add(name)
        self.recompute_tasks.add(name)

    async def recompute_changed_on(self, connection, *, domain: str, key: str, change_id: str,
                                   task: str, payload: Mapping[str, Any],
                                   scheduled_at: datetime | None = None,
                                   subject_id: str | None = None, matter_id: str | None = None,
                                   payload_version: int = 1, data_generation: str | None = None):
        """Persist a distinct change and native wake in the caller's transaction.

        ``change_id`` is the original domain change identity, not a sequence
        high-water mark. Replaying it cannot advance the semantic revision.
        """
        if not all(isinstance(value, str) and value for value in (domain, key, change_id)):
            raise AppError("invalid_recompute_identity")
        if task not in self.recompute_routes:
            raise AppError("task_not_recompute", status=409)
        data_generation = (await self._bind_generation_on(connection,
            JobSpec(task, key, payload, data_generation=data_generation))).data_generation
        _identity_digest(JobSpec(task, key, payload, subject_id=subject_id, matter_id=matter_id,
                                 payload_version=payload_version, data_generation=data_generation))
        await connection.execute(
            "INSERT INTO runtime.recompute_work(domain,work_key,task,payload,subject_id,matter_id,"
            "payload_version,data_generation) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (domain, key, task, Jsonb(dict(payload)), subject_id, matter_id, payload_version, data_generation))
        row = await (await connection.execute(
            "SELECT * FROM runtime.recompute_work WHERE data_generation=%s AND domain=%s AND work_key=%s FOR UPDATE",
            (data_generation, domain, key))).fetchone()
        if (row["task"] != task or row["payload"] != dict(payload)
                or row["subject_id"] != subject_id or row["matter_id"] != matter_id
                or row["payload_version"] != payload_version or row["data_generation"] != data_generation):
            raise AppError("recompute_identity_conflict", status=409)
        existing = await (await connection.execute(
            "SELECT revision FROM runtime.recompute_changes WHERE data_generation=%s AND domain=%s AND work_key=%s AND change_id=%s",
            (data_generation, domain, key, change_id))).fetchone()
        if existing:
            return existing["revision"]
        revision = row["dirty_revision"] + 1
        await connection.execute(
            "INSERT INTO runtime.recompute_changes(data_generation,domain,work_key,change_id,revision) VALUES(%s,%s,%s,%s,%s)",
            (data_generation, domain, key, change_id, revision))
        # NULL denotes immediate execution. Never postpone an existing wake.
        previous_due = row["due_at"] if row["dirty_revision"] > row["processed_revision"] else scheduled_at
        due = None if previous_due is None or scheduled_at is None else min(previous_due, scheduled_at)
        await connection.execute(
            "UPDATE runtime.recompute_work SET dirty_revision=%s,due_at=%s,last_error=NULL "
            "WHERE data_generation=%s AND domain=%s AND work_key=%s", (revision, due, data_generation, domain, key))
        if row["claim_token"] is None and row["queued_job_key"] is None:
            await self._schedule_recompute_on(connection, row, due)
        elif row["queued_job_key"] is not None:
            queued = await (await connection.execute(
                "UPDATE procrastinate_jobs SET scheduled_at=%s WHERE id=(SELECT job_id FROM runtime.job_keys WHERE key=%s) "
                "AND status='todo' AND (scheduled_at IS NOT NULL AND (%s::timestamptz IS NULL OR scheduled_at>%s)) "
                "RETURNING id,queue_name", (due, row["queued_job_key"], due, due))).fetchone()
            if queued:
                await self._notify_pending(connection, queued["id"], queued["queue_name"])
        return revision

    async def _schedule_recompute_on(self, connection, row, due):
        generation = row["queue_generation"] + 1
        identity = hashlib.sha256(json.dumps([row["data_generation"], row["domain"], row["work_key"]],
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        job_key = f"recompute:{identity}:{generation}"
        await self.enqueue(connection, JobSpec(
            row["task"], job_key, {"domain": row["domain"], "key": row["work_key"], "generation": generation},
            scheduled_at=due, subject_id=row["subject_id"], matter_id=row["matter_id"],
            payload_version=row["payload_version"], data_generation=row["data_generation"]))
        await connection.execute(
            "UPDATE runtime.recompute_work SET queue_generation=%s,queued_job_key=%s,due_at=%s "
            "WHERE data_generation=%s AND domain=%s AND work_key=%s",
            (generation, job_key, due, row["data_generation"], row["domain"], row["work_key"]))

    async def _claim_recompute(self, domain, key, generation, lease_seconds):
        token = str(uuid4())
        async with self.database.transaction() as connection:
            row = await (await connection.execute(
                "UPDATE runtime.recompute_work SET claim_token=%s,claim_revision=dirty_revision,"
                "claim_expires_at=now()+(%s * interval '1 second'),queued_job_key=NULL "
                "WHERE domain=%s AND work_key=%s AND queue_generation=%s "
                "AND dirty_revision>processed_revision AND data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "AND (claim_token IS NULL OR claim_expires_at<=now()) "
                "RETURNING dirty_revision,payload,data_generation", (token, lease_seconds, domain, key, generation))).fetchone()
            if row:
                await connection.execute(
                    "INSERT INTO runtime.recompute_attempts(claim_token,data_generation,domain,work_key,revision) VALUES(%s,%s,%s,%s,%s)",
                    (token, row["data_generation"], domain, key, row["dirty_revision"]))
        return RecomputeClaim(domain, key, row["dirty_revision"], token, row["payload"], row["data_generation"]) if row else None

    async def _complete_recompute_on(self, connection, claim, apply):
        await self.assert_generation_on(connection, claim.data_generation)
        row = await (await connection.execute(
            "SELECT *,claim_expires_at>now() AS claim_valid FROM runtime.recompute_work "
            "WHERE data_generation=%s AND domain=%s AND work_key=%s FOR UPDATE",
            (claim.data_generation, claim.domain, claim.key))).fetchone()
        if not row or row["claim_token"] != claim.token or not row["claim_valid"]:
            raise AppError("recompute_claim_lost", status=409)
        if row["dirty_revision"] != claim.revision:
            await connection.execute(
                "UPDATE runtime.recompute_attempts SET outcome='superseded',finished_at=now() WHERE claim_token=%s",
                (claim.token,))
            await connection.execute(
                "UPDATE runtime.recompute_work SET claim_token=NULL,claim_revision=NULL,claim_expires_at=NULL "
                "WHERE data_generation=%s AND domain=%s AND work_key=%s", (claim.data_generation, claim.domain, claim.key))
            await self._schedule_recompute_on(connection, row, row["due_at"])
            return False
        coverage = await apply(connection, claim)
        if coverage is None:
            coverage = RecomputeCoverage()
        if not isinstance(coverage, RecomputeCoverage) or set(coverage.isolated) & set(coverage.repaired):
            raise ValueError("invalid_recompute_coverage")
        for material_id, reason in coverage.isolated.items():
            if not isinstance(material_id, str) or not material_id or not isinstance(reason, str) or not reason:
                raise ValueError("invalid_recompute_gap")
            await connection.execute(
                "INSERT INTO runtime.recompute_gaps(data_generation,domain,work_key,material_id,revision,reason) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(data_generation,domain,work_key,material_id) DO UPDATE SET "
                "revision=CASE WHEN runtime.recompute_gaps.repaired_at IS NULL "
                "THEN LEAST(runtime.recompute_gaps.revision,excluded.revision) ELSE excluded.revision END,"
                "reason=excluded.reason,repaired_at=NULL", (claim.data_generation, claim.domain, claim.key, material_id, claim.revision, reason))
            await connection.execute(
                "INSERT INTO runtime.recompute_gap_history(data_generation,domain,work_key,material_id,revision,action,reason) "
                "VALUES(%s,%s,%s,%s,%s,'isolated',%s)", (claim.data_generation, claim.domain, claim.key, material_id, claim.revision, reason))
        if coverage.repaired:
            await connection.execute(
                "INSERT INTO runtime.recompute_gap_history(data_generation,domain,work_key,material_id,revision,action,reason) "
                "SELECT data_generation,domain,work_key,material_id,%s,'repaired',reason FROM runtime.recompute_gaps "
                "WHERE data_generation=%s AND domain=%s AND work_key=%s AND material_id=ANY(%s) AND repaired_at IS NULL",
                (claim.revision, claim.data_generation, claim.domain, claim.key, list(coverage.repaired)))
            await connection.execute(
                "UPDATE runtime.recompute_gaps SET repaired_at=now() WHERE data_generation=%s AND domain=%s AND work_key=%s "
                "AND material_id=ANY(%s) AND repaired_at IS NULL", (claim.data_generation, claim.domain, claim.key, list(coverage.repaired)))
        gap = await (await connection.execute(
            "SELECT min(revision) AS first_gap FROM runtime.recompute_gaps "
            "WHERE data_generation=%s AND domain=%s AND work_key=%s AND repaired_at IS NULL",
            (claim.data_generation, claim.domain, claim.key))).fetchone()
        covered = min(claim.revision, gap["first_gap"] - 1) if gap["first_gap"] is not None else claim.revision
        await connection.execute(
            "UPDATE runtime.recompute_work SET processed_revision=%s,covered_revision=%s,"
            "claim_token=NULL,claim_revision=NULL,claim_expires_at=NULL,due_at=NULL,last_error=NULL "
            "WHERE data_generation=%s AND domain=%s AND work_key=%s",
            (claim.revision, covered, claim.data_generation, claim.domain, claim.key))
        await connection.execute(
            "UPDATE runtime.recompute_attempts SET outcome='succeeded',finished_at=now() WHERE claim_token=%s",
            (claim.token,))
        return True

    async def retry_recompute_on(self, connection, domain, key, *, due_at=None):
        """Explicitly resume a failed, still-uncovered logical work item."""
        row = await (await connection.execute(
            "SELECT * FROM runtime.recompute_work WHERE data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
            "AND domain=%s AND work_key=%s FOR UPDATE",
            (domain, key))).fetchone()
        if not row or row["dirty_revision"] <= row["processed_revision"]:
            return False
        if row["claim_token"] or row["queued_job_key"]:
            return False
        await connection.execute("UPDATE runtime.recompute_work SET last_error=NULL WHERE data_generation=%s AND domain=%s AND work_key=%s",
                                 (row["data_generation"], domain, key))
        await self._schedule_recompute_on(connection, row, due_at)
        return True

    async def recover_recompute_due(self, *, limit=128):
        """Recover only expired claims/due work; rows and receipts are authoritative."""
        if self.database is None or type(limit) is not int or not 1 <= limit <= 512:
            raise ValueError("invalid_recompute_recovery")
        if not self.recompute_tasks:
            return 0
        async with self.database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT w.*,j.status AS queue_status FROM runtime.recompute_work w "
                "LEFT JOIN runtime.job_keys k ON k.key=w.queued_job_key "
                "LEFT JOIN procrastinate_jobs j ON j.id=k.job_id "
                "WHERE w.task=ANY(%s) AND w.data_generation=(SELECT id FROM runtime.installation_generation WHERE singleton=true) "
                "AND w.dirty_revision>w.processed_revision AND w.last_error IS NULL "
                "AND (w.due_at IS NULL OR w.due_at<=now()) "
                "AND (w.claim_token IS NULL OR w.claim_expires_at<=now()) "
                "AND (w.queued_job_key IS NULL OR j.status NOT IN ('todo','doing') OR j.id IS NULL "
                "OR (w.claim_token IS NOT NULL AND w.claim_expires_at<=now())) "
                "ORDER BY w.due_at NULLS FIRST,w.domain,w.work_key LIMIT %s FOR UPDATE OF w SKIP LOCKED",
                (sorted(self.recompute_tasks), limit))).fetchall()
            count = 0
            for row in rows:
                # Only the owning process has registered this handler.
                if row["claim_token"] is not None:
                    await connection.execute(
                        "UPDATE runtime.recompute_attempts SET outcome='expired',finished_at=now() "
                        "WHERE claim_token=%s AND outcome='running'", (row["claim_token"],))
                await connection.execute(
                    "UPDATE runtime.recompute_work SET claim_token=NULL,claim_revision=NULL,claim_expires_at=NULL "
                    "WHERE data_generation=%s AND domain=%s AND work_key=%s",
                    (row["data_generation"], row["domain"], row["work_key"]))
                await self._schedule_recompute_on(connection, row, row["due_at"])
                count += 1
        return count

    async def _coalesce_pending(self, connection, spec, queueing_lock):
        # Only an explicitly identified wake may merge. An unlabelled command,
        # input, result or physical attempt is never adopted by a wake.
        rows = await (await connection.execute(
            "SELECT j.id,j.task_name,j.args,j.queue_name,j.scheduled_at,j.queueing_lock,j.priority,"
            "k.subject_id,k.matter_id,k.payload_version FROM procrastinate_jobs j "
            "JOIN LATERAL (SELECT subject_id,matter_id,payload_version,data_generation FROM runtime.job_keys "
            "WHERE job_id=j.id AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true "
            "WHERE k.data_generation=%s AND j.status='todo' "
            "AND j.queueing_lock=%s "
            "ORDER BY j.id FOR UPDATE OF j",
            (spec.data_generation, queueing_lock))).fetchall()
        if not rows:
            return None
        if any(row["task_name"] != spec.task or row["args"] != dict(spec.payload)
               or row["subject_id"] != spec.subject_id or row["matter_id"] != spec.matter_id
               or row["payload_version"] != spec.payload_version for row in rows):
            raise AppError("job_identity_conflict", status=409)
        kept = rows[0]
        dates = [spec.scheduled_at, *(row["scheduled_at"] for row in rows)]
        due = None if None in dates else min(dates)
        priority = max(kept["priority"], 100 if spec.service_class == ServiceClass.DUE else 0)
        if due != kept["scheduled_at"] or priority != kept["priority"]:
            await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=%s,priority=%s "
                "WHERE id=%s AND status='todo'", (due, priority, kept["id"]))
            await self._notify_pending(connection, kept["id"], kept["queue_name"])
        return kept["id"]

    @staticmethod
    async def _notify_pending(connection, job_id, queue):
        payload = json.dumps({"type": "job_inserted", "job_id": job_id})
        await connection.execute(
            "SELECT pg_notify(%s,%s),pg_notify('procrastinate_any_queue_v1',%s)",
            ("procrastinate_queue_v1#" + queue, payload, payload))

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
            await self._notify_pending(connection, job_id, lane.value)
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

    async def route_reassigned_pending(self, database, role, *, limit=128):
        """Move only unclaimed old control jobs whose task acquired a new owner."""
        if type(limit) is not int or not 1 <= limit <= 512:
            raise ValueError("invalid_job_routing_batch_size")
        targets = {task: lane for task, lane in TASK_LANES.items()
                   if lane in {ExecutionLane.LIFE_CONTROL, ExecutionLane.CAPABILITY_CONTROL}
                   and role_for_lane(lane) == role and task in self.tasks}
        if not targets:
            return 0
        async with database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT id,task_name FROM procrastinate_jobs WHERE status='todo' AND queue_name='control' "
                "AND task_name=ANY(%s) ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
                (list(targets), limit))).fetchall()
            for row in rows:
                lane = targets[row["task_name"]].value
                await connection.execute("UPDATE procrastinate_jobs SET queue_name=%s WHERE id=%s", (lane, row["id"]))
                await self._notify_pending(connection, row["id"], lane)
        return len(rows)

    async def cancel(self, connection, key: str):
        while True:
            row = await (await connection.execute(
                "SELECT job_id FROM runtime.job_keys WHERE key=%s", (key,))).fetchone()
            if not row:
                return False
            job_id = row["job_id"]
            job = await (await connection.execute(
                "SELECT id FROM procrastinate_jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone() if job_id else None
            current = await (await connection.execute(
                "SELECT job_id,cancelled_at FROM runtime.job_keys WHERE key=%s FOR UPDATE", (key,))).fetchone()
            if current["job_id"] != job_id:
                continue
            if current["cancelled_at"] is not None:
                return True
            await connection.execute("UPDATE runtime.job_keys SET cancelled_at=now() WHERE key=%s", (key,))
            remaining = await (await connection.execute(
                "SELECT 1 FROM runtime.job_keys WHERE job_id=%s AND cancelled_at IS NULL LIMIT 1", (job_id,))).fetchone()
            if job and not remaining:
                await self.app.job_manager.cancel_job_by_id_async(job_id, abort=True, connection=connection)
            return True
