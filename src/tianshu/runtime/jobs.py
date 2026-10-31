"""One real Procrastinate queue with business changes and enqueue in one transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import procrastinate

from tianshu.types import ServiceClass
from .errors import AppError


@dataclass(frozen=True, slots=True)
class JobSpec:
    task: str
    key: str
    payload: Mapping[str, Any]
    service_class: ServiceClass = ServiceClass.ELASTIC
    scheduled_at: datetime | None = None
    subject_id: str | None = None
    matter_id: str | None = None


class JobQueue:
    def __init__(self, dsn: str, *, max_connections=4):
        self.app = procrastinate.App(connector=procrastinate.PsycopgConnector(
            conninfo=dsn, min_size=0, max_size=max_connections))
        self.tasks = {}

    async def open(self):
        await self.app.open_async()

    async def close(self):
        await self.app.close_async()

    def register(self, name, handler, *, retryable=False):
        """Only explicitly reentrant computation handlers may retry automatically."""
        if name in self.tasks:
            raise ValueError("duplicate_task_registration")
        self.tasks[name] = self.app.task(name=name, retry=3 if retryable else False)(handler)
        return self.tasks[name]

    async def enqueue(self, connection, spec: JobSpec) -> int:
        if spec.task not in self.tasks:
            raise AppError("task_not_registered", status=503)
        if spec.service_class not in {ServiceClass.DUE, ServiceClass.ELASTIC}:
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
        job_id = await self.tasks[spec.task].configure(
            connection=connection, queue=spec.service_class.value,
            schedule_at=spec.scheduled_at,
            # Future work never shares a global actor lock that blocks earlier deadlines.
            priority=100 if spec.service_class == ServiceClass.DUE else 0,
        ).defer_async(**dict(spec.payload))
        await connection.execute("UPDATE runtime.job_keys SET job_id=%s WHERE key=%s",
                                 (job_id, spec.key))
        return job_id

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
