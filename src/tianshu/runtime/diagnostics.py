"""Read actual jobs, process resources and Loki; no parallel operational state ledger."""

import asyncio
from datetime import UTC, datetime
import os
import ssl

import aiohttp

from .errors import AppError
from .execution import job_observation
from .rb_logs_query import LogQuery
from .rb_logs_output import secret_values


class RuntimeDiagnostics:
    def __init__(self, database, models, settings, *, events=None):
        self.database, self.models, self.settings = database, models, settings
        self.events = events

    async def tasks(self, *, limit=50):
        limit = max(1, min(int(limit), 100))
        async with self.database.transaction(control=True, read_only=True) as conn:
            jobs = await (
                await conn.execute(
                    "SELECT id,task_name,queue_name,status,scheduled_at,attempts,abort_requested,"
                    "coalesce(scheduled_at>now(),false) AS scheduled "
                    "FROM procrastinate_jobs ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()
            counts = await (
                await conn.execute(
                    "SELECT queue_name,status,count(*) AS count "
                    "FROM procrastinate_jobs GROUP BY queue_name,status"
                )
            ).fetchall()
        return {"jobs": [job_observation(row) for row in jobs], "counts": counts,
                "limit": limit, "complete": len(jobs) < limit}

    async def resources(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            processes = await (
                await conn.execute(
                    "SELECT role,ready,registration,heartbeat_at "
                    "FROM runtime.processes ORDER BY heartbeat_at DESC LIMIT 100"
                )
            ).fetchall()
        try:
            broker = await self.models.snapshot()
        except (AppError, aiohttp.ClientError, TimeoutError):
            broker = {"status": "unavailable", "quota_groups": None}
        return {"processes": processes, "broker": broker}

    async def _log_pipeline(self):
        config = self.settings.get("observability", {})
        names = {item.get("name") for item in config.get("processes", ()) if isinstance(item, dict)}
        loki_url = config.get("loki_url")

        async def probe(url, *, headers=None, tls=None):
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                    async with session.get(url, headers=headers, ssl=tls, allow_redirects=False) as response:
                        return "available" if response.status == 200 else "unavailable"
            except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
                return "unavailable"

        async def loki():
            if not loki_url:
                return "not_configured"
            try:
                headers = {"X-Scope-OrgID": config.get("tenant", "tianshu")}
                if config.get("query_token_ref"):
                    headers["Authorization"] = "Bearer " + self.settings.secrets.resolve(config["query_token_ref"])
                tls = ssl.create_default_context(cafile=config.get("ca_file"))
            except (OSError, ValueError, AppError, KeyError):
                return "unavailable"
            return await probe(loki_url.rstrip("/") + "/ready", headers=headers, tls=tls)

        async def vector():
            if "vector" not in names:
                return "not_configured"
            # This endpoint is the exporter configured by deploy/core/observability.
            # Reachability is not proof of any individual Loki delivery.
            return await probe("http://127.0.0.1:18687/metrics")

        loki_state, collector_state = await asyncio.gather(loki(), vector())
        return {"loki": {"status": loki_state},
                "collector": {"status": collector_state, "checkpoint": "vector_managed",
                              "collected_sequence": None, "delivered_sequence": None,
                              "reason": "per_source_delivery_not_exposed"}}

    async def logs(self, *, start_ns, end_ns, role=None, level=None, module=None,
                   correlation_id=None, trace_id=None, job_id=None, attempt_id=None,
                   keyword=None, exclude_conversation=False, cursor=None, limit=100):
        query = self._log_query()
        pipeline = asyncio.create_task(self._log_pipeline())
        try:
            try:
                result = await asyncio.to_thread(
                    query.page, start_ns=start_ns, end_ns=end_ns, role=role, level=level,
                    module=module, correlation_id=correlation_id, trace_id=trace_id,
                    job_id=job_id, attempt_id=attempt_id, keyword=keyword,
                    exclude_conversation=exclude_conversation, cursor=cursor, limit=limit,
                )
            except AppError as exc:
                if exc.code not in {"local_log_directory_unavailable", "log_snapshot_unavailable"}:
                    raise
                result = {"entries": [], "next_cursor": None, "streams": [], "next_end_ns": None,
                          "coverage": {"complete": False, "snapshot_complete": False,
                                       "source": "local_append_files", "missing": [exc.code],
                                       "has_more": False},
                          "freshness": {"as_of": datetime.now(UTC).isoformat(), "snapshot_at": None,
                                        "late_arrivals": "refresh_with_new_snapshot", "writers": [],
                                        "disk": {"total_bytes": None, "free_bytes": None}}}
            result["freshness"].update(await pipeline)
            if self.events:
                result["freshness"]["current_writer"] = self.events.status()
            return result
        finally:
            if not pipeline.done():
                pipeline.cancel()
                await asyncio.gather(pipeline, return_exceptions=True)

    def _log_query(self):
        environ = dict(os.environ)
        configured = getattr(getattr(self.settings, "secrets", None), "environ", None)
        if configured is not None:
            environ.update(configured)
        return LogQuery(self.settings.artifact_directory.parent / "logs", secrets=secret_values(environ))

    async def log_changes(self, *, checkpoint=None, limit=500):
        """Expose durable source-position changes for an idempotent statistics projection.

        Consumers commit the returned checkpoint with their projection and dedupe
        on event_id. A checkpoint never claims every generated event was persisted.
        """
        query = self._log_query()
        try:
            result = await asyncio.to_thread(query.changes, checkpoint=checkpoint, limit=limit)
        except AppError as exc:
            if exc.code != "local_log_directory_unavailable":
                raise
            result = {"entries": [], "next_checkpoint": checkpoint,
                      "coverage": {"complete": False, "missing": [exc.code],
                                   "source": "local_append_files", "caught_up": False},
                      "freshness": {"as_of": datetime.now(UTC).isoformat(), "writers": []}}
        if self.events:
            result["freshness"]["current_writer"] = self.events.status()
        return result
